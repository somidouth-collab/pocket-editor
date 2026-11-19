"""Background, non-executing Python/JSON syntax checks for the selected tab."""
import json
import queue
import threading
from tkinter import ttk

MAX_CHARACTERS = 200000
PYTHON_SUFFIXES = {'.py', '.pyw', '.pyi'}


def check_syntax(source, suffix):
    suffix = suffix.lower()
    if suffix not in PYTHON_SUFFIXES | {'.json'}:
        return {'status': 'unsupported', 'message': 'Syntax checks support saved Python and JSON filenames.'}
    if len(source) > MAX_CHARACTERS:
        return {'status': 'skipped', 'message': 'Syntax check skipped: file exceeds 200,000 characters.'}
    try:
        if suffix == '.json':
            json.loads(source)
        else:
            # Compile validates syntax and scope rules without executing user code.
            compile(source, '<editor-buffer>', 'exec', dont_inherit=True)
    except SyntaxError as error:
        return {'status': 'error', 'message': error.msg, 'line': error.lineno or 1, 'column': error.offset or 1}
    except json.JSONDecodeError as error:
        return {'status': 'error', 'message': error.msg, 'line': error.lineno, 'column': error.colno}
    except (ValueError, RecursionError, MemoryError) as error:
        return {'status': 'skipped', 'message': f'Could not check syntax: {str(error) or type(error).__name__}'}
    return {'status': 'ok', 'message': 'Syntax OK · JSON' if suffix == '.json' else 'Syntax OK · Python'}


class Diagnostics:
    def __init__(self, api):
        self.api, self.editor = api, api._editor
        self.root = self.editor.root
        self.requests, self.results = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.pending = self.poll_job = None
        self.automatic, self.busy = True, False
        self.result, self.identity_checked = None, None
        self.footer = ttk.Frame(self.editor.editor_panel, padding=(10, 4))
        self.label = ttk.Label(self.footer, cursor='hand2', wraplength=600)
        self.label.pack(fill='x')
        self.label.bind('<Button-1>', lambda event: self.jump())
        self.footer.bind('<Configure>', lambda event: self.label.configure(wraplength=max(100, event.width - 20)))
        threading.Thread(target=self.worker, daemon=True).start()
        self.poll_job = self.root.after(100, self.poll)
        api.on('document_changed', self.changed)
        api.on('document_switched', self.changed)
        api.on('document_saved', self.changed)
        api.on_cleanup(self.cleanup)
        api.add_command('Check Syntax', lambda: self.request(manual=True))
        api.add_command('Go to Syntax Error', self.jump)
        api.add_command('Toggle Automatic Syntax Checks', self.toggle)
        api.provide_service('get_status', self.status)
        api.provide_service('check', lambda: self.request(manual=True))
        self.changed()

    def identity(self):
        return self.editor.document_generation, self.editor.document_revision, self.api.get_path()

    def status(self):
        return {'automatic': self.automatic, 'busy': self.busy,
                'result': dict(self.result) if self.result else None}

    @staticmethod
    def replace_queue(target, item):
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        try:
            target.put_nowait(item)
        except queue.Full:
            pass

    def worker(self):
        while not self.stopped.is_set():
            try:
                identity, source, suffix, manual = self.requests.get(timeout=.1)
            except queue.Empty:
                continue
            result = check_syntax(source, suffix)
            if not self.stopped.is_set():
                self.replace_queue(self.results, (identity, result, manual))

    def clear(self):
        self.editor.buffer.tag_remove('diagnostic_error', '1.0', 'end')
        self.footer.grid_remove()
        self.result, self.identity_checked = None, None

    def changed(self, **payload):
        self.clear()
        self.busy = False
        if self.pending:
            self.root.after_cancel(self.pending)
            self.pending = None
        if self.automatic:
            self.pending = self.root.after(450, self.request)

    def request(self, manual=False):
        if self.pending:
            self.root.after_cancel(self.pending)
            self.pending = None
        self.clear()
        path = self.api.get_path()
        suffix = path.suffix.lower() if path else ''
        source = self.api.get_text()
        self.busy = True
        self.replace_queue(self.requests, (self.identity(), source, suffix, manual))

    def poll(self):
        self.poll_job = None
        try:
            identity, result, manual = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            if identity == self.identity() and (self.automatic or manual):
                self.busy = False
                self.result, self.identity_checked = result, identity
                self.display(result, manual)
                path = self.api.get_path()
                self.api.emit('syntax_diagnostics.updated', path=str(path) if path else None, result=dict(result))
        if not self.stopped.is_set():
            self.poll_job = self.root.after(100, self.poll)

    def display(self, result, manual):
        text = self.editor.buffer
        error = result['status'] == 'error'
        if error:
            line, column = result['line'], result['column']
            contents = text.get(f'{line}.0', f'{line}.end')
            tk_column = int(text.tk.call('string', 'length', contents[:column - 1]))
            start = text.index(f'{line}.{tk_column}')
            end = text.index(f'{start} +1c')
            if text.compare(start, '>=', f'{line}.end'):
                start, end = f'{line}.0', f'{line}.end'
            text.tag_configure('diagnostic_error', underline=True, underlinefg='#f48771')
            text.tag_add('diagnostic_error', start, end)
            text.tag_raise('diagnostic_error')
            text.tag_raise('sel')
            message = f'Ln {line}, Col {column} · {result["message"]} · Click to jump'
        else:
            message = result['message']
        if error or manual or result['status'] == 'skipped':
            self.label.configure(text=message, foreground='#f48771' if error else '#8e9ba9')
            self.footer.grid(row=2, column=0, columnspan=3, sticky='ew')

    def jump(self):
        if self.identity_checked == self.identity() and self.result and self.result['status'] == 'error':
            line, column = self.result['line'], self.result['column']
            contents = self.editor.buffer.get(f'{line}.0', f'{line}.end')
            column = int(self.editor.buffer.tk.call('string', 'length', contents[:column - 1]))
            self.editor.buffer.mark_set('insert', f'{line}.{column}')
            self.editor.buffer.see('insert')
            self.editor.buffer.focus_set()
            self.editor.refresh()
        else:
            self.request(manual=True)

    def toggle(self):
        self.automatic = not self.automatic
        self.changed()

    def cleanup(self):
        self.stopped.set()
        for job in (self.pending, self.poll_job):
            if job:
                self.root.after_cancel(job)
        self.pending = self.poll_job = None
        for document in self.editor.documents:
            document['widget'].tag_remove('diagnostic_error', '1.0', 'end')
        self.footer.destroy()


def register(api):
    Diagnostics(api)
