"""Local web-language bridge, installation and typed completion edits (no Tk)."""
from pathlib import Path
import json
import os
import queue
import shutil
import subprocess
import threading

TOOLS = Path(__file__).resolve().parent.parent / 'tools' / 'web'
LANGUAGES = {'.html':'html','.htm':'html','.css':'css','.scss':'scss','.less':'less',
             '.js':'javascript','.mjs':'javascript','.cjs':'javascript','.jsx':'javascriptreact',
             '.ts':'typescript','.tsx':'typescriptreact'}
DEFAULTS = dict(automatic=True, delay=250, indent=2, width=100, tabs=False, singleQuote=False, semi=True)


def language(path):
    return LANGUAGES.get(Path(path).suffix.lower()) if path else None


def read_settings(path):
    settings=DEFAULTS.copy()
    try:
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data,dict):return settings
        for key,default in settings.items():
            value=data.get(key)
            if isinstance(default,bool) and type(value) is bool:settings[key]=value
        for key,lo,hi in (('delay',100,1000),('width',60,200)):
            if type(data.get(key)) is int and lo<=data[key]<=hi:settings[key]=data[key]
        if type(data.get('indent')) is int and data['indent'] in (2,4):settings['indent']=data['indent']
    except (OSError,ValueError):pass
    return settings


def index(source, offset):
    prefix=source[:offset]
    return f'{prefix.count(chr(10))+1}.{len(prefix.rsplit(chr(10),1)[-1].encode("utf-16-le"))//2}'


class WebWorker:
    def __init__(self):
        self.process=None;self.responses=None;self.lock=threading.Lock();self.stopped=False

    def stop_process(self):
        with self.lock:
            process=self.process;self.process=None
        if process is not None:
            if process.poll() is None:process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin,process.stdout):
                if stream:stream.close()

    def close(self):
        self.stopped=True;self.stop_process()

    def start(self):
        with self.lock:
            if self.stopped:raise RuntimeError('Web Tools stopped.')
            if self.process is not None and self.process.poll() is None:return
            node=shutil.which('node')
            if not node:raise RuntimeError('Install Node.js LTS, restart the editor, then click Install / Repair Tools.')
            if not (TOOLS/'node_modules/prettier/package.json').is_file():
                raise RuntimeError('Web tools are not installed. Click Install / Repair Tools in the Web Tools panel.')
            process=subprocess.Popen([node,str(TOOLS/'bridge.cjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
            responses=queue.Queue(maxsize=2);self.process=process;self.responses=responses
            def read():
                try:
                    for line in process.stdout:
                        responses.put(json.loads(line))
                except (OSError,ValueError):pass
                finally:
                    try:responses.put_nowait({'error':'Web language service stopped. Try Install / Repair Tools.'})
                    except queue.Full:pass
            threading.Thread(target=read,daemon=True,name='web-language-reader').start()

    def request(self, payload):
        self.start();process=self.process;responses=self.responses
        try:
            process.stdin.write(json.dumps(payload)+'\n');process.stdin.flush()
            result=responses.get(timeout=20)
        except (OSError,ValueError,queue.Empty,AttributeError) as error:
            self.stop_process();raise RuntimeError('Web language service timed out or disconnected; retry the operation.') from error
        if 'error' in result:raise RuntimeError(result['error'])
        return result['result']

    def install(self):
        self.stop_process()
        node=shutil.which('node');npm=shutil.which('npm')
        if not node or not npm:raise RuntimeError('Install Node.js LTS (includes npm), restart Pocket Editor, then retry.')
        # Launch npm's JS entry point directly on Windows; never build a cmd shell string.
        if os.name=='nt':
            cli=Path(npm).parent/'node_modules/npm/bin/npm-cli.js'
            if not cli.is_file():raise RuntimeError('Cannot find npm-cli.js beside npm. Repair the Node.js installation.')
            command=[node,str(cli)]
        else:command=[npm]
        with self.lock:
            if self.stopped:raise RuntimeError('Web Tools stopped.')
            process=subprocess.Popen(command+['ci','--prefix',str(TOOLS),'--ignore-scripts','--no-audit','--no-fund'],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
            self.process=process
        try:
            output,_=process.communicate(timeout=180)
            if process.returncode:raise RuntimeError(output[-2000:] or 'Web tool installation failed.')
            return 'Web tools installed. Formatting and autocomplete are ready.'
        except subprocess.TimeoutExpired:
            process.kill();process.communicate();raise RuntimeError('Web tool installation timed out.') from None
        finally:
            with self.lock:
                if self.process is process:self.process=None
