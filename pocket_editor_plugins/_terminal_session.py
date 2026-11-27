"""Local PTY shell transport. Reader/writer threads never call Tk."""
from pathlib import Path
import importlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading

TOOLS=Path(__file__).resolve().parent.parent/'tools'/'terminal'


def engine():
    paths=[TOOLS/'.venv'/'Lib'/'site-packages']+list((TOOLS/'.venv'/'lib').glob('python*/site-packages'))
    for path in paths:
        if path.is_dir() and str(path) not in sys.path:sys.path.append(str(path))
    try:
        pyte=importlib.import_module('pyte')
        transport=importlib.import_module('winpty' if os.name=='nt' else 'ptyprocess')
        return pyte,transport
    except ImportError as error:
        raise RuntimeError('Use Terminal → Install / Repair Engine to enable the interactive shell.') from error


def shell_command(program=''):
    if not program:
        program=(shutil.which('pwsh') or os.environ.get('COMSPEC','cmd.exe')) if os.name=='nt' else os.environ.get('SHELL','/bin/sh')
    executable=shutil.which(program) or (program if Path(program).is_file() else None)
    if not executable:raise RuntimeError('Shell executable not found. Choose one in Settings → Shell…')
    if os.name=='nt':
        return [executable]+(['-NoLogo'] if Path(executable).stem.lower() in ('powershell','pwsh') else [])
    return [executable,'-i']


class TerminalSession:
    def __init__(self):
        self.messages=queue.Queue(maxsize=64);self.inputs=queue.Queue(maxsize=32)
        self.stopping=threading.Event();self.process=None;self.lock=threading.Lock();self.encoding='utf-8'
        self.rows,self.columns=24,80

    def emit(self,item):
        while not self.stopping.is_set():
            try:self.messages.put(item,timeout=.1);return
            except queue.Full:pass

    def start(self,directory,program='',rows=24,columns=80,encoding='utf-8',force_color=False):
        self.encoding=encoding;self.rows,self.columns=rows,columns
        _pyte,transport=engine();command=shell_command(program)
        if os.name!='nt':
            self.start_posix(directory,command,encoding,force_color);return
        environment=os.environ.copy();environment.update(TERM='xterm-256color',COLORTERM='truecolor')
        if force_color:environment.update(FORCE_COLOR='1',CLICOLOR_FORCE='1')
        def read():
            process=None
            try:
                process=transport.PtyProcess.spawn(command,cwd=str(directory),env=environment,dimensions=(rows,columns))
                with self.lock:
                    self.process=process
                    stopping=self.stopping.is_set()
                if stopping:self.close();return
                self.emit(('started',process.pid));self.resize(self.rows,self.columns)
                threading.Thread(target=self.write_loop,daemon=True,name='pocket-terminal-input').start()
                while not self.stopping.is_set():
                    data=process.read(4096)
                    if not data:
                        if process.isalive():continue
                        break
                    self.emit(('data',data))
            except EOFError:pass
            except Exception as error:
                if not self.stopping.is_set():self.emit(('error',str(error)))
            finally:
                code=None
                self.emit(('exit',code))
        threading.Thread(target=read,daemon=True,name='pocket-terminal-output').start()

    def start_posix(self,directory,command,encoding,force_color):
        def read():
            try:
                with self.lock:
                    if self.stopping.is_set():return
                    self.process=subprocess.Popen([sys.executable,'-u',str(TOOLS/'host.py')],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                        text=True,encoding='utf-8',bufsize=1,start_new_session=True)
                    process=self.process
                config=dict(directory=str(directory),command=command,encoding=encoding,force_color=force_color,
                            rows=self.rows,columns=self.columns)
                process.stdin.write(json.dumps(config)+'\n');process.stdin.flush()
                threading.Thread(target=self.write_loop,daemon=True,name='pocket-terminal-input').start()
                for line in process.stdout:
                    kind,value=json.loads(line);self.emit((kind,value))
                process.wait()
            except (OSError,ValueError) as error:
                if not self.stopping.is_set():self.emit(('error',str(error)))
        threading.Thread(target=read,daemon=True,name='pocket-terminal-output').start()

    def write(self,value):
        if self.stopping.is_set():return False
        if len(value)>65536:raise ValueError('Paste is limited to 64,000 characters per action.')
        try:self.inputs.put_nowait(value);return True
        except queue.Full:return False

    def write_loop(self):
        while not self.stopping.is_set():
            try:value=self.inputs.get(timeout=.1)
            except queue.Empty:continue
            try:
                if os.name=='nt':self.process.write(value)
                else:
                    if isinstance(value,tuple):messages=[['resize',*value]]
                    else:messages=[['input',value[i:i+1024]] for i in range(0,len(value),1024)]
                    for message in messages:
                        if self.stopping.is_set():return
                        self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()
            except (OSError,EOFError,ValueError):return

    def resize(self,rows,columns):
        self.rows,self.columns=max(2,rows),max(10,columns)
        if self.process is not None:
            if os.name!='nt':
                try:self.inputs.put_nowait((self.rows,self.columns))
                except queue.Full:pass
            else:
                try:self.process.setwinsize(self.rows,self.columns)
                except (OSError,ValueError,EOFError):pass

    def close(self):
        self.stopping.set()
        with self.lock:process=self.process
        if process is None:return
        if os.name=='nt':
            try:process.terminate(force=True);process.close(force=True)
            except (OSError,EOFError,AttributeError):pass
        else:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            for stream in (process.stdin,process.stdout):
                if stream:
                    try:stream.close()
                    except (OSError,ValueError):pass



def make_screen(pyte,columns,rows,send):
    import copy
    class Screen(pyte.HistoryScreen):
        def __init__(self):
            self.alternate=None
            super().__init__(columns,rows,history=2000,ratio=.25)
        def set_mode(self,*modes,**kwargs):
            if kwargs.get('private') and any(mode in (47,1047,1049) for mode in modes) and self.alternate is None:
                self.alternate=(self.buffer,copy.deepcopy(self.cursor),self.history,self.margins,self.lines,self.columns)
                fresh=pyte.HistoryScreen(self.columns,self.lines,history=2000,ratio=.25)
                self.buffer,self.cursor,self.history,self.margins=fresh.buffer,fresh.cursor,fresh.history,fresh.margins
                self.cursor_position();self.dirty.update(range(self.lines))
            super().set_mode(*modes,**kwargs)
        def reset_mode(self,*modes,**kwargs):
            super().reset_mode(*modes,**kwargs)
            if kwargs.get('private') and any(mode in (47,1047,1049) for mode in modes) and self.alternate is not None:
                rows,cols=self.lines,self.columns
                self.buffer,self.cursor,self.history,self.margins,self.lines,self.columns=self.alternate;self.alternate=None
                self.resize(lines=rows,columns=cols);self.dirty.update(range(rows))
        def write_process_input(self,value):send(value)
    return Screen()
