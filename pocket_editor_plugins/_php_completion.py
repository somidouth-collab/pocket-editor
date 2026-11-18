"""Completion settings, snippet edits and background scheduling; no Tk dependencies."""
from collections import OrderedDict
from dataclasses import dataclass
import copy
import hashlib
import json
from pathlib import Path
import queue
import re
import threading
import time

from pocket_editor_plugins._php_tools import (
    TOOLS, LanguageServer, CompletionCancelled, completion_edits)

DEFAULTS = {'automatic': True, 'delay_ms': 120, 'min_chars': 2, 'max_items': 150, 'projects': {}}
VERSIONS = ('7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5')


def read_settings(path):
    result = copy.deepcopy(DEFAULTS)
    try:
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return result
    if not isinstance(saved, dict):
        return result
    if type(saved.get('automatic')) is bool:
        result['automatic'] = saved['automatic']
    for key, low, high in (('delay_ms', 50, 1000), ('min_chars', 1, 4), ('max_items', 20, 500)):
        if type(saved.get(key)) is int and low <= saved[key] <= high:
            result[key] = saved[key]
    projects = saved.get('projects')
    if isinstance(projects, dict):
        for root, project in list(projects.items())[:200]:
            if not isinstance(root, str) or not isinstance(project, dict):
                continue
            version = project.get('php_version', '8.4')
            paths = project.get('include_paths', [])
            result['projects'][root] = {
                'php_version': version if version in VERSIONS else '8.4',
                'include_paths': [value for value in paths[:128] if isinstance(value, str) and value]
                    if isinstance(paths, list) else []}
    return result


def server_config(settings, root):
    project = settings['projects'].get(str(root), {})
    return {'environment': {'phpVersion': project.get('php_version', '8.4') + '.0',
                'includePaths': [str((Path(root) / value).resolve())
                                 for value in project.get('include_paths', [])]},
            'completion': {'insertUseDeclaration': True}, 'files': {'maxSize': 2000000,
                'associations': ['*.php', '*.phtml', '*.php3', '*.php4', '*.php5', '*.php7', '*.php8', '*.phps']}}


def ranked_items(items, source, cursor, maximum):
    """Keep server context, prefer exact-case prefixes, preserve LSP sortText."""
    match = re.search(r'[$\w]*$', source[:cursor])
    prefix = match.group().lstrip('$')
    def order(item):
        candidate = str(item.get('filterText', item['label'])).lstrip('$')
        score = 0 if candidate.startswith(prefix) else 1 if candidate.casefold().startswith(prefix.casefold()) else 2
        return score, str(item.get('sortText', item['label'])), item['label']
    valid = [item for item in items if isinstance(item, dict) and isinstance(item.get('label'), str)]
    return sorted(valid, key=order)[:maximum]


def render_snippet(value):
    """Decode numeric LSP tabstops/defaults/choices without executing variables."""
    output, stops = [], []
    def expand(fragment, depth=0):
        if depth > 12:
            raise ValueError('Snippet nesting is too deep')
        index = 0
        while index < len(fragment):
            character = fragment[index]
            if character == '\\' and index + 1 < len(fragment) and fragment[index+1] in '\\$}':
                output.append(fragment[index+1]); index += 2; continue
            if character != '$':
                output.append(character); index += 1; continue
            simple = re.match(r'\$(\d+)', fragment[index:])
            if simple:
                position = len(output); stops.append((int(simple[1]), position, position))
                index += len(simple[0]); continue
            if fragment[index:index+2] != '${':
                # PHP dollar variables are literal unless they use a snippet construct.
                output.append('$'); index += 1; continue
            end, level = index + 2, 1
            while end < len(fragment) and level:
                if fragment[end] == '\\':
                    end += 2; continue
                if fragment[end] == '{': level += 1
                if fragment[end] == '}': level -= 1
                end += 1
            if level:
                raise ValueError('Unclosed completion placeholder')
            body = fragment[index+2:end-1]
            header = re.match(r'^(\d+)(.*)$', body, re.S)
            if not header:
                raise ValueError('Snippet variables/transforms are not supported')
            number, rest = int(header[1]), header[2]
            start = len(output)
            if rest.startswith(':'):
                expand(rest[1:], depth + 1)
            elif rest.startswith('|') and rest.endswith('|'):
                choice = re.split(r'(?<!\\),', rest[1:-1])[0]
                output.extend(choice.replace('\\,', ',').replace('\\|', '|'))
            elif rest:
                raise ValueError('Snippet transforms are not supported')
            stops.append((number, start, len(output)))
            index = end
    expand(value)
    return ''.join(output), sorted(stops, key=lambda item: (item[0] == 0, item[0]))


def prepare_completion(source, cursor, item):
    item = copy.deepcopy(item)
    stops = []
    if item.get('insertTextFormat', 1) == 2:
        if item.get('textEdit'):
            value = item['textEdit']['newText']
        else:
            value = item.get('insertText', item['label'])
        expanded, stops = render_snippet(value)
        if item.get('textEdit'):
            item['textEdit']['newText'] = expanded
        else:
            item['insertText'] = expanded
        item['insertTextFormat'] = 1
    numbers = [number for number, _, _ in stops if number != 0]
    if len(numbers) != len(set(numbers)):
        raise ValueError('Mirrored snippet placeholders are not supported')
    edits, caret = completion_edits(source, cursor, item)
    primary = next(edit for edit in edits if edit[3])
    start = caret - len(primary[2])
    ranges = [(number, start + begin, start + end) for number, begin, end in stops]
    return edits, ranges[0][1] if ranges else caret, ranges


@dataclass(frozen=True)
class Capture:
    path: Path
    source: str
    generation: int
    revision: int
    index: str
    cursor: int

    @property
    def identity(self):
        return self.path, self.generation, self.revision, self.index


class CompletionWorker:
    """One session, latest request wins, bounded exact-result cache and cancellation."""
    def __init__(self):
        self.stopped = threading.Event()
        self.tasks = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=2)
        self.lock = threading.Lock()
        self.latest = 0
        self.server = None
        self.files = set()
        self.reindex = False
        self.cache = OrderedDict()
        self.had_items = False
        self.metrics = {'cache_hits': 0, 'cancelled_requests': 0, 'last_latency_ms': 0,
                        'phase': 'idle', 'workspace': '', 'persistent_cache': False}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def cancel(self):
        with self.lock:
            self.latest += 1
        try:
            self.tasks.get_nowait()
        except queue.Empty:
            pass

    def submit(self, kind, capture, directory, config, item=None):
        self.cancel()
        with self.lock:
            ticket = self.latest
        self.tasks.put_nowait((ticket, kind, capture, directory, config, item, time.monotonic()))
        return ticket

    def invalidate(self, path=None, reindex=False):
        with self.lock:
            if path is not None:
                self.files.add(Path(path).resolve())
            self.reindex |= reindex

    def _publish(self, value):
        try:
            self.results.put_nowait(value)
        except queue.Full:
            try:
                self.results.get_nowait()
            except queue.Empty:
                pass
            self.results.put_nowait(value)

    def _run(self):
        while not self.stopped.is_set():
            try:
                ticket, kind, capture, directory, config, item, started = self.tasks.get(timeout=.05)
            except queue.Empty:
                continue
            cancel = lambda: self.stopped.is_set() or ticket != self.latest
            try:
                with self.lock:
                    files, dirty = list(self.files), self.reindex
                    self.files.clear(); self.reindex = False
                if self.server is None or self.server.root != directory or self.server.config != config or dirty or self.server.process.poll() is not None:
                    if self.server is not None:
                        self.server.close(); self.server = None
                    self.cache.clear(); self.had_items = False
                    self.metrics.update(phase='starting', workspace=str(directory))
                    self.server = LanguageServer(directory, self.stopped, config=config, cache_dir=TOOLS/'cache')
                    self.metrics['persistent_cache'] = self.server.persistent_cache
                if files:
                    self.cache.clear()
                    self.server.notify('workspace/didChangeWatchedFiles', {'changes': [
                        {'uri': path.as_uri(), 'type': 2} for path in files]})
                if cancel():
                    raise CompletionCancelled()
                if len(capture.source.encode('utf-8')) > 2000000:
                    raise ValueError('PHP completion supports documents up to 2 MB.')
                self.server.sync(capture.path, capture.source)
                if kind == 'warm':
                    self.metrics['phase'] = 'ready'
                    self._publish((ticket, kind, capture, None, ''))
                    continue
                self.metrics['phase'] = 'completing'
                if kind in ('resolve', 'detail'):
                    value = self.server.resolve(item, cancel=cancel)
                else:
                    digest = hashlib.sha256(capture.source.encode('utf-8')).digest()
                    key = (str(capture.path), digest, capture.cursor)
                    cached = self.cache.get(key)
                    if cached and time.monotonic() - cached[0] < 3:
                        self.metrics['cache_hits'] += 1
                        self.cache.move_to_end(key)
                        value = cached[1]
                    else:
                        value = self.server.complete(capture.path, capture.source, capture.cursor, cancel=cancel)
                        # Allow the server's asynchronous document analysis to settle,
                        # especially after a watched imported file changed. Never impose
                        # the old fixed multi-second empty-result delay on a warm session.
                        now = time.monotonic()
                        settle = now + (1.0 if files else .18)
                        deadline = max(now + (2 if files else .3),
                                       self.server.started + 12 if not self.had_items else 0)
                        while not value and time.monotonic() < deadline:
                            if time.monotonic() >= settle and self.server.progress_seen.is_set() and not self.server.indexing.is_set():
                                break
                            if cancel():
                                raise CompletionCancelled()
                            if self.stopped.wait(.12):
                                raise CompletionCancelled()
                            value = self.server.complete(capture.path, capture.source, capture.cursor, cancel=cancel)
                        self.had_items |= bool(value)
                        if value and not self.server.last_incomplete:
                            self.cache[key] = (time.monotonic(), value)
                            while len(self.cache) > 8:
                                self.cache.popitem(last=False)
                if cancel():
                    raise CompletionCancelled()
                self.metrics.update(phase='ready', last_latency_ms=round((time.monotonic()-started)*1000, 1))
                self._publish((ticket, kind, capture, value, ''))
            except CompletionCancelled:
                self.metrics['cancelled_requests'] += 1
                self.metrics['phase'] = 'ready' if self.server else 'idle'
            except Exception as error:
                self.metrics['phase'] = 'error'
                if ticket == self.latest:
                    self._publish((ticket, kind, capture, None, str(error)))
                if isinstance(error, (OSError, RuntimeError)):
                    if self.server is not None:
                        self.server.close(); self.server = None
                    self.cache.clear()
        if self.server is not None:
            self.server.close(); self.server = None

    def close(self):
        self.stopped.set()
        self.cancel()
        current = self.server
        if current is not None and current.process is not None:
            try:
                current.process.terminate()
            except OSError:
                pass

    def status(self):
        current = self.server
        return {**self.metrics, 'server_pid': current.process.pid if current else None,
                'indexing': current.indexing.is_set() if current else False}
