"""C/C++ tool discovery, formatting, syntax checks and a small clangd LSP client."""
import copy
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import unicodedata

from pocket_editor_plugins._php_tools import position_at

EXTENSIONS = {'.c', '.h', '.cc', '.cpp', '.cxx', '.c++', '.hh', '.hpp', '.hxx', '.ipp', '.tpp'}
STYLES = ('file', 'LLVM', 'Google', 'Chromium', 'Mozilla', 'WebKit', 'Microsoft', 'GNU')
DEFAULTS = dict(clang_format='', clangd='', compiler='', gdb='', style='file', automatic=True,
                syntax_automatic=True, delay_ms=250, cpp_standard='c++20', c_standard='c17',
                includes=[], compile_commands_dir='')
MAX_SOURCE = 4 * 1024 * 1024


def is_cpp(path):
    return path is not None and Path(path).suffix.lower() in EXTENSIONS


def language(path):
    return 'c' if Path(path).suffix == '.c' else 'cpp'


def read_settings(path):
    settings = copy.deepcopy(DEFAULTS)
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value, dict):return settings
        for key in ('clang_format', 'clangd', 'compiler', 'gdb', 'compile_commands_dir'):
            if isinstance(value.get(key), str):settings[key] = value[key][:4096]
        for key in ('automatic', 'syntax_automatic'):
            if type(value.get(key)) is bool:settings[key] = value[key]
        if value.get('style') in STYLES:settings['style'] = value['style']
        if type(value.get('delay_ms')) is int and 100 <= value['delay_ms'] <= 1500:settings['delay_ms'] = value['delay_ms']
        if value.get('cpp_standard') in ('c++11', 'c++14', 'c++17', 'c++20', 'c++23'):settings['cpp_standard'] = value['cpp_standard']
        if value.get('c_standard') in ('c99', 'c11', 'c17', 'c23'):settings['c_standard'] = value['c_standard']
        if isinstance(value.get('includes'), list):
            settings['includes'] = [item for item in value['includes'][:100] if isinstance(item, str) and item.strip()]
    except (OSError, ValueError):pass
    return settings


def executable(name, configured=''):
    if configured.strip():
        candidate = configured.strip().strip('"')
        found = shutil.which(candidate)
        if found:return found
        raise RuntimeError(f'Cannot find {name}: {candidate}. Select an installed executable in C/C++ Settings.')
    names = [name]
    if name in ('clangd', 'clang-format'):names += [f'{name}-{version}' for version in range(23, 13, -1)]
    for candidate in names:
        found = shutil.which(candidate)
        if found:return found
    if os.name == 'nt' and name in ('clangd', 'clang-format'):
        for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
            candidate = Path(os.environ.get(variable, 'C:/Program Files')) / 'LLVM/bin' / (name + '.exe')
            if candidate.is_file():return str(candidate)
    raise RuntimeError(f'{name} is not installed or is not on PATH. Open C/C++ Settings → How to use for installation commands.')


def compiler(settings, path):
    if settings['compiler']:return executable('C/C++ compiler', settings['compiler'])
    choices = ('gcc', 'clang') if language(path) == 'c' else ('g++', 'clang++')
    for name in choices:
        try:return executable(name)
        except RuntimeError:pass
    raise RuntimeError('Install GCC/G++ or Clang, or choose a GCC-compatible compiler in C/C++ Settings.')


def project_root(path, selected=None):
    path = Path(path).resolve()
    if selected:
        root = Path(selected).resolve()
        if root.is_dir() and path.is_relative_to(root):return root
    for root in path.parents:
        if any((root / name).exists() for name in ('compile_commands.json', '.clangd', 'CMakeLists.txt', '.git')):return root
    return path.parent


def flags(settings, path, root):
    standard = settings['c_standard'] if language(path) == 'c' else settings['cpp_standard']
    values = ['-std=' + standard]
    values += ['-I' + str((Path(root) / item).resolve()) for item in settings['includes']]
    return values


def run_process(command, source=None, cwd=None, cancelled=lambda: False, timeout=30):
    """No shell; cancellation and cleanup apply to this owned process only."""
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if source is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + timeout
    supplied = False
    try:
        while True:
            if cancelled():raise RuntimeError('Operation cancelled.')
            if time.monotonic() > deadline:raise RuntimeError(f'Tool exceeded {timeout} seconds.')
            try:
                out, err = process.communicate(input=source if not supplied else None, timeout=.1)
                return process.returncode, out, err
            except subprocess.TimeoutExpired:supplied = True
    finally:
        if process.poll() is None:process.kill()
        process.communicate()


def format_source(source, path, settings, cancelled=lambda: False):
    command = [executable('clang-format', settings['clang_format']), '--style=' + settings['style'],
               '--fallback-style=LLVM', '--assume-filename=' + str(Path(path).resolve())]
    code, out, err = run_process(command, source, str(Path(path).parent), cancelled)
    if code:raise RuntimeError(err.strip() or 'clang-format failed. The document was left intact.')
    return out


def check_source(source, path, settings, root, cancelled=lambda: False):
    command = [compiler(settings, path), '-x', 'c' if language(path) == 'c' else 'c++',
               *flags(settings, path, root), '-iquote', str(Path(path).parent),
               '-fsyntax-only', '-fdiagnostics-color=never', '-fno-diagnostics-show-caret', '-Wall', '-Wextra', '-']
    code, out, err = run_process(command, source, str(root), cancelled)
    diagnostics = []
    for line in err.splitlines():
        match = re.match(r'(.+?):(\d+):(\d+):\s*(fatal error|error|warning|note):\s*(.*)', line)
        if not match:continue
        name, row, column, severity, message = match.groups()
        target = Path(path) if name in ('<stdin>', '-') else Path(name)
        if not target.is_absolute():target = Path(root) / target
        if severity == 'note':continue
        selected_column = int(column)
        if target.resolve() == Path(path).resolve():
            lines = source.split('\n')
            if 1 <= int(row) <= len(lines):
                # Compiler columns are display columns; tabs advance to multiples of eight.
                display = 1;characters = 0
                for character in lines[int(row)-1]:
                    if display >= selected_column:break
                    display += (8-(display-1)%8) if character == '\t' else (2 if unicodedata.east_asian_width(character) in ('W','F') else 1)
                    characters += 1
                selected_column = characters+1
        diagnostics.append(dict(path=str(target.resolve()), line=int(row), column=selected_column,
                                severity=severity, message=message))
    if code and not diagnostics:raise RuntimeError(err.strip() or out.strip() or 'The compiler could not check this file.')
    return diagnostics[:500]


def build_source(path, settings, root, cancelled=lambda: False):
    destination = Path(root) / '.pocket-debug'
    destination.mkdir(exist_ok=True)
    if destination.is_symlink():raise ValueError('The debug build directory cannot be a symbolic link.')
    target = destination / (Path(path).stem + ('.exe' if os.name == 'nt' else ''))
    if target.is_symlink():raise ValueError('The debug executable cannot be a symbolic link.')
    command = [compiler(settings, path), '-g', '-O0', *flags(settings, path, root), str(Path(path).resolve()), '-o', str(target)]
    code, out, err = run_process(command, cwd=str(root), cancelled=cancelled, timeout=60)
    if code:raise RuntimeError(err.strip() or out.strip() or 'Debug build failed.')
    return str(target)


class Clangd:
    def __init__(self, root, settings, cancelled=lambda: False, path=None):
        self.language = language(path or Path(root) / 'file.cpp')
        self.root = Path(root);self.settings = copy.deepcopy(settings)
        self.process = None;self.responses = queue.Queue(maxsize=128)
        self.sequence = 0;self.uri = None;self.source = None;self.version = 0
        command = [executable('clangd', settings['clangd']), '--background-index', '--log=error']
        if settings['compile_commands_dir']:
            directory = (self.root / settings['compile_commands_dir']).resolve()
            if not directory.is_dir():raise ValueError('Choose an existing compilation database directory.')
            command.append('--compile-commands-dir=' + str(directory))
        try:
            self.process = subprocess.Popen(command, cwd=str(self.root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.reader = threading.Thread(target=self._read, daemon=True);self.reader.start()
            result = self.request('initialize', dict(processId=os.getpid(), rootUri=self.root.as_uri(),
                workspaceFolders=[dict(uri=self.root.as_uri(), name=self.root.name)],
                capabilities={'general': {'positionEncodings': ['utf-16']},
                    'textDocument': {'completion': {'completionItem': {'snippetSupport': True,
                        'insertReplaceSupport': True}}}},
                initializationOptions={'fallbackFlags': flags(settings, path or self.root / 'file.cpp', self.root)}), cancelled, timeout=25)
            if result.get('capabilities', {}).get('positionEncoding', 'utf-16') != 'utf-16':
                raise RuntimeError('clangd selected an unsupported position encoding.')
            self.notify('initialized', {})
        except Exception:
            self.close();raise

    def _read(self):
        try:
            while self.process.poll() is None:
                headers = {}
                while True:
                    line = self.process.stdout.readline(8192)
                    if not line:raise EOFError('clangd closed its output.')
                    if line in (b'\n', b'\r\n'):break
                    key, value = line.decode('ascii').split(':', 1);headers[key.lower()] = value.strip()
                size = int(headers.get('content-length', 0))
                if not 0 < size <= 16 * 1024 * 1024:raise ValueError('Invalid clangd message size.')
                # BufferedReader.read obtains exactly the requested bytes or EOF.
                raw = self.process.stdout.read(size)
                if len(raw) != size:raise EOFError('Incomplete clangd message.')
                message = json.loads(raw)
                if 'id' in message:self.responses.put(message, timeout=1)
        except (OSError, EOFError, ValueError, queue.Full):pass

    def send(self, value):
        data = json.dumps(dict(jsonrpc='2.0', **value)).encode('utf-8')
        self.process.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode() + data)
        self.process.stdin.flush()

    def notify(self, method, params):self.send(dict(method=method, params=params))

    def request(self, method, params, cancelled=lambda: False, timeout=12):
        self.sequence += 1;ticket = self.sequence
        self.send(dict(id=ticket, method=method, params=params))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled():
                self.notify('$/cancelRequest', {'id': ticket});raise RuntimeError('Operation cancelled.')
            if self.process.poll() is not None:raise RuntimeError('clangd stopped. Check its executable and project settings.')
            try:message = self.responses.get(timeout=.05)
            except queue.Empty:continue
            if message.get('method'):
                result = [] if message['method'] == 'workspace/configuration' else None
                self.send(dict(id=message['id'], result=result))
            elif message.get('id') == ticket:
                if 'error' in message:raise RuntimeError(message['error'].get('message', 'clangd request failed.'))
                return message.get('result') or {}
        raise RuntimeError('clangd timed out. The project may still be indexing; try again.')

    def complete(self, path, source, offset, cancelled=lambda: False):
        uri = Path(path).resolve().as_uri()
        if uri != self.uri:
            if self.uri:self.notify('textDocument/didClose', {'textDocument': {'uri': self.uri}})
            self.uri = uri;self.version = 1
            self.notify('textDocument/didOpen', {'textDocument': {'uri': uri, 'languageId': language(path),
                'version': self.version, 'text': source}})
        elif source != self.source:
            self.version += 1
            self.notify('textDocument/didChange', {'textDocument': {'uri': uri, 'version': self.version},
                                                'contentChanges': [{'text': source}]})
        self.source = source
        result = self.request('textDocument/completion', {'textDocument': {'uri': uri},
            'position': position_at(source, offset), 'context': {'triggerKind': 1}}, cancelled)
        items = result.get('items', []) if isinstance(result, dict) else result
        return [item for item in items if isinstance(item, dict) and isinstance(item.get('label'), str)][:150]

    def close(self):
        if self.process is None:return
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:stream.close()


class ToolWorker:
    """Lazy single worker; completion requests replace older pending completions."""
    def __init__(self):
        self.tasks = queue.Queue(maxsize=8);self.results = queue.Queue(maxsize=16)
        self.stopped = threading.Event();self.thread = None;self.server = None;self.latest = 0

    def submit(self, ticket, kind, capture, settings, root):
        previous = self.latest
        if kind == 'complete':self.latest = ticket
        try:self.tasks.put_nowait((ticket, kind, capture, copy.deepcopy(settings), root))
        except queue.Full:
            self.latest = previous;raise
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True);self.thread.start()

    def _run(self):
        try:
            while not self.stopped.is_set():
                try:ticket, kind, capture, settings, root = self.tasks.get(timeout=.1)
                except queue.Empty:continue
                cancelled = lambda: self.stopped.is_set() or (kind == 'complete' and ticket != self.latest)
                if cancelled():continue
                try:
                    path, source, offset = capture[:3]
                    if kind == 'format':value = format_source(source, path, settings, cancelled)
                    elif kind == 'syntax':value = check_source(source, path, settings, root, cancelled)
                    elif kind == 'build':value = build_source(path, settings, root, cancelled)
                    else:
                        if (self.server is None or self.server.root != root or self.server.settings != settings or
                                self.server.process.poll() is not None or self.server.language != language(path)):
                            if self.server:self.server.close()
                            self.server = None
                            self.server = Clangd(root, settings, self.stopped.is_set, path)
                        value = self.server.complete(path, source, offset, cancelled)
                    result = (ticket, kind, capture, value, '')
                except Exception as error:result = (ticket, kind, capture, None, str(error))
                if not cancelled():
                    while not self.stopped.is_set():
                        try:self.results.put(result,timeout=.1);break
                        except queue.Full:pass
        finally:
            if self.server:self.server.close()

    def close(self):
        self.stopped.set()
