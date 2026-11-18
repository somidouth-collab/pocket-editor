"""Shared PHP tool discovery and LSP transport. No Tk calls belong in this module."""
import json
import hashlib
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

TOOLS = Path(__file__).resolve().parent.parent / 'tools' / 'php'
PHP_SUFFIXES = {'.php', '.phtml', '.php3', '.php4', '.php5', '.php7', '.php8', '.phps'}
SETUP_HELP = 'Install Node.js, then run: npm ci --prefix tools/php --ignore-scripts'


def is_php(path):
    return path is not None and Path(path).suffix.lower() in PHP_SUFFIXES


def node_command():
    node = shutil.which('node') or shutil.which('node.exe')
    if not node:
        raise RuntimeError('PHP tools need Node.js. ' + SETUP_HELP)
    return node


def server_command():
    package = TOOLS / 'node_modules' / 'intelephense'
    try:
        metadata = json.loads((package / 'package.json').read_text(encoding='utf-8'))
        binary = metadata['bin']
        if isinstance(binary, dict):
            binary = binary['intelephense']
        script = package / binary
        if not script.is_file():
            raise OSError('Missing server entry point')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('PHP completion tools are missing. ' + SETUP_HELP) from error
    return [node_command(), str(script), '--stdio']


def project_root(path, selected=None):
    """Prefer the containing Explorer project, then Composer/git, then file folder."""
    path = Path(path).resolve()
    if selected:
        selected = Path(selected).resolve()
        try:
            path.relative_to(selected)
            if selected.is_dir():
                return selected
        except ValueError:
            pass
    for parent in path.parents:
        if (parent / 'composer.json').is_file() or (parent / '.git').exists():
            return parent
    return path.parent


def position_at(source, offset):
    prefix = source[:offset]
    line = prefix.count('\n')
    tail = prefix.rsplit('\n', 1)[-1]
    return {'line': line, 'character': len(tail.encode('utf-16-le')) // 2}


def offset_at(source, position):
    line, units = position['line'], position['character']
    if type(line) is not int or type(units) is not int or line < 0 or units < 0:
        raise ValueError('Invalid completion position')
    lines = source.split('\n')
    if line >= len(lines):
        raise ValueError('Completion line is outside the document')
    offset = sum(len(value) + 1 for value in lines[:line])
    used = 0
    for index, character in enumerate(lines[line]):
        if used == units:
            return offset + index
        used += 2 if ord(character) > 0xffff else 1
        if used > units:
            raise ValueError('Completion splits a Unicode character')
    if used != units:
        raise ValueError('Completion column is outside the line')
    return offset + len(lines[line])


def completion_edits(source, cursor, item):
    """Validate all edits against one source snapshot before touching Tk."""
    import re
    if item.get('insertTextFormat', 1) != 1:
        raise ValueError('Snippet completions are not supported')
    primary = item.get('textEdit')
    if primary:
        span = primary.get('range') or primary.get('replace')
        if not span:
            raise ValueError('Missing completion replacement range')
        edits = [(offset_at(source, span['start']), offset_at(source, span['end']), primary['newText'], True)]
    else:
        prefix = re.search(r'[$\w\\]*$', source[:cursor])
        edits = [(prefix.start(), cursor, item.get('insertText', item['label']), True)]
    for edit in item.get('additionalTextEdits', []):
        span = edit['range']
        edits.append((offset_at(source, span['start']), offset_at(source, span['end']), edit['newText'], False))
    edits.sort(key=lambda edit: (edit[0], edit[1]))
    previous = -1
    previous_span = None
    for start, end, value, primary in edits:
        if start < previous or start > end or not isinstance(value, str) or previous_span == (start, end):
            raise ValueError('Invalid or overlapping completion edits')
        previous = end
        previous_span = (start, end)
    primary = next(edit for edit in edits if edit[3])
    caret = primary[0] + len(primary[2]) + sum(len(value) - (end - start)
              for start, end, value, main in edits if not main and end <= primary[0])
    return edits, caret


class CompletionCancelled(Exception):
    """A superseded completion is normal control flow, not a server failure."""


class LanguageServer:
    """A single PHP workspace session, called only by its background worker."""
    def __init__(self, root, stopped=None, config=None, cache_dir=None):
        self.root = Path(root).resolve()
        self.stopped = stopped or threading.Event()
        self.messages = queue.Queue(maxsize=256)
        self.notices = queue.Queue(maxsize=32)
        self.process = None
        self.uri = None
        self.source = None
        self.version = 0
        self.sequence = 0
        self.config = config or {}
        self.indexing = threading.Event()
        self.progress_seen = threading.Event()
        self.progress_tokens = set()
        self.started = time.monotonic()
        self.can_resolve = False
        self.last_incomplete = False
        self.cache = None
        self.persistent_cache = False
        self.cache_lock = None
        if cache_dir is not None:
            try:
                # Only one live server owns a workspace cache. Other editor windows
                # fall back to a temporary workspace instead of sharing writable state.
                fingerprint = hashlib.sha256((str(self.root) + json.dumps(self.config, sort_keys=True)
                    + (TOOLS / 'node_modules/intelephense/package.json').read_text()).encode()).hexdigest()[:24]
                storage = Path(cache_dir) / fingerprint
                storage.mkdir(parents=True, exist_ok=True)
                lock = storage / 'session.lock'
                if os.name == 'posix' and lock.exists():
                    try:
                        owner = lock.read_text(encoding='ascii')
                        process_id = int(owner)
                        if process_id > 0:
                            try:
                                os.kill(process_id, 0)
                            except ProcessLookupError:
                                if lock.read_text(encoding='ascii') == owner:
                                    lock.unlink()
                    except (OSError, ValueError):
                        pass
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
                    stream.write(str(os.getpid()))
                self.cache_lock = lock
                storage_path = str(storage)
                self.persistent_cache = True
            except OSError:
                self.persistent_cache = False
        if not self.persistent_cache:
            self.cache = tempfile.TemporaryDirectory(prefix='pocket-php-lsp-')
            storage_path = self.cache.name
        try:
            self.process = subprocess.Popen(server_command(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(self.root),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            initialized = self.request('initialize', {'processId': os.getpid(), 'rootUri': self.root.as_uri(),
                'workspaceFolders': [{'uri': self.root.as_uri(), 'name': self.root.name}],
                'capabilities': {'workspace': {'configuration': True}, 'window': {'workDoneProgress': True},
                    'textDocument': {'completion': {'completionItem': {'snippetSupport': True, 'documentationFormat': ['plaintext', 'markdown'],
                        'resolveSupport': {'properties': ['documentation', 'detail', 'additionalTextEdits']},
                        'insertReplaceSupport': True}}, 'publishDiagnostics': {}}},
                'initializationOptions': {'storagePath': storage_path,
                    'globalStoragePath': storage_path}}, timeout=30)
            provider = (initialized or {}).get('capabilities', {}).get('completionProvider', {})
            self.can_resolve = bool(provider.get('resolveProvider'))
            self.notify('initialized', {})
            self.notify('workspace/didChangeConfiguration', {'settings': {'intelephense': {
                'telemetry': {'enabled': False}, 'completion': {'insertUseDeclaration': True},
                'files': {'maxSize': 2000000}, **self.config}}})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            stream = self.process.stdout
            while not self.stopped.is_set():
                headers = {}
                while True:
                    line = stream.readline(8192)
                    if not line:
                        raise EOFError('PHP language server closed its output')
                    if line in (b'\r\n', b'\n'):
                        break
                    key, value = line.decode('ascii').split(':', 1)
                    headers[key.lower()] = value.strip()
                length = int(headers.get('content-length', '0'))
                if not 0 < length <= 16 * 1024 * 1024:
                    raise ValueError('Invalid PHP language server message length')
                data = stream.read(length)
                if len(data) != length:
                    raise EOFError('Incomplete PHP language server message')
                message = json.loads(data)
                if message.get('method') == '$/progress':
                    params = message.get('params', {})
                    value = params.get('value', {})
                    if isinstance(value, dict):
                        token = str(params.get('token', ''))
                        if value.get('kind') == 'begin':
                            self.progress_seen.set(); self.progress_tokens.add(token); self.indexing.set()
                        elif value.get('kind') == 'end':
                            self.progress_tokens.discard(token)
                            if not self.progress_tokens:
                                self.indexing.clear()
                # Surface user-facing server notifications; logs/diagnostics are not completion UI.
                if message.get('method') == 'window/showMessage':
                    try:
                        self.notices.put_nowait(message.get('params', {}))
                    except queue.Full:
                        pass
                if 'id' in message:
                    while not self.stopped.is_set():
                        try:
                            self.messages.put(message, timeout=.1)
                            break
                        except queue.Full:
                            pass
        except (OSError, ValueError, EOFError) as error:
            try:
                self.messages.put_nowait({'transport_error': str(error)})
            except queue.Full:
                pass

    def send(self, message):
        if self.stopped.is_set():
            raise RuntimeError('PHP completion stopped')
        body = json.dumps({'jsonrpc': '2.0', **message}, ensure_ascii=False).encode('utf-8')
        self.process.stdin.write(f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii') + body)
        self.process.stdin.flush()

    def notify(self, method, params):
        self.send({'method': method, 'params': params})

    def request(self, method, params, timeout=12, cancel=None):
        self.sequence += 1
        identifier = self.sequence
        self.send({'id': identifier, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while not self.stopped.is_set():
            if cancel is not None and cancel():
                self.notify("$/cancelRequest", {"id": identifier})
                raise CompletionCancelled()
            if time.monotonic() >= deadline:
                self.notify('$/cancelRequest', {'id': identifier})
                raise RuntimeError('PHP completion timed out; try again after indexing.')
            try:
                message = self.messages.get(timeout=.025)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError('PHP language server exited unexpectedly')
                continue
            if 'transport_error' in message:
                raise RuntimeError(message['transport_error'])
            if 'method' in message:
                if message['method'] == 'window/showMessageRequest':
                    self.notices.put_nowait(message.get('params', {}))
                    result = None
                elif message['method'] == 'workspace/configuration':
                    result = []
                    for item in message.get('params', {}).get('items', []):
                        section = item.get('section', '')
                        config = {'intelephense': {'telemetry': {'enabled': False},
                            'completion': {'insertUseDeclaration': True}, **self.config}}
                        value = config
                        for key in section.split('.') if section else []:
                            value = value.get(key) if isinstance(value, dict) else None
                        result.append(value)
                else:
                    result = None
                self.send({'id': message['id'], 'result': result})
            elif message.get('id') == identifier:
                if 'error' in message:
                    raise RuntimeError(message['error'].get('message', 'PHP completion failed'))
                return message.get('result')
        raise RuntimeError('PHP completion stopped')

    def sync(self, path, source):
        uri = Path(path).resolve().as_uri()
        if uri != self.uri:
            if self.uri:
                self.notify('textDocument/didClose', {'textDocument': {'uri': self.uri}})
            self.version = 1
            self.notify('textDocument/didOpen', {'textDocument': {'uri': uri,
                'languageId': 'php', 'version': self.version, 'text': source}})
            self.uri, self.source = uri, source
        elif source != self.source:
            self.version += 1
            self.notify('textDocument/didChange', {'textDocument': {'uri': uri, 'version': self.version},
                                                  'contentChanges': [{'text': source}]})
            self.source = source

    def complete(self, path, source, cursor, cancel=None):
        self.sync(path, source)
        result = self.request('textDocument/completion', {'textDocument': {'uri': self.uri},
            'position': position_at(source, cursor), 'context': {'triggerKind': 1}}, cancel=cancel)
        self.last_incomplete = bool(result.get('isIncomplete')) if isinstance(result, dict) else False
        return result.get('items', []) if isinstance(result, dict) else result or []

    def resolve(self, item, cancel=None):
        if not self.can_resolve:
            return item
        return self.request('completionItem/resolve', item, cancel=cancel) or item

    def close(self):
        # Termination also interrupts an outstanding request; no GUI-thread protocol wait.
        process = self.process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        if self.cache is not None:
            self.cache.cleanup()
        if self.cache_lock is not None:
            self.cache_lock.unlink(missing_ok=True)
            self.cache_lock = None
