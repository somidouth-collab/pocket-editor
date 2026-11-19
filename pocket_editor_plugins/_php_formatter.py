"""Background PHP formatting using local Prettier + its PHP parser."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from pocket_editor_plugins._php_tools import TOOLS, SETUP_HELP, is_php, node_command


class Formatter:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def format(self, source, filename, cursor=0, width=100, tabs=4, php_version='8.4'):
        if not (TOOLS / 'node_modules' / 'prettier').is_dir():
            raise RuntimeError('PHP formatter tools are missing. ' + SETUP_HELP)
        payload = json.dumps({'source': source, 'filename': str(filename), 'cursor': cursor,
                              'width': width, 'tabs': tabs, 'phpVersion': php_version})
        with self.lock:
            if self.stopped.is_set():
                raise RuntimeError('PHP formatter stopped')
            current = subprocess.Popen([node_command(), str(TOOLS / 'format.cjs')],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.process = current
        try:
            try:
                output, error = current.communicate(payload, timeout=30)
            except subprocess.TimeoutExpired:
                current.kill(); current.communicate()
                raise RuntimeError('PHP formatting exceeded 30 seconds; document left intact.') from None
            if current.returncode:
                raise RuntimeError(error.strip() or 'PHP formatter stopped')
            result = json.loads(output)
            if not isinstance(result.get('formatted'), str) or type(result.get('cursorOffset')) is not int:
                raise RuntimeError('Invalid PHP formatter response')
            return result
        finally:
            with self.lock:
                self.process = None

    def close(self):
        self.stopped.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.kill()


def register(api):
    editor, root = api._editor, api._editor.root
    tool = Formatter()
    api.on_cleanup(tool.close)
    results = queue.Queue(maxsize=1)
    settings_path = Path(__file__).with_name('php_formatter_settings.json')
    width, php_version = 100, '8.4'
    try:
        saved = json.loads(settings_path.read_text(encoding='utf-8'))
        if isinstance(saved, dict):
            if type(saved.get('print_width')) is int and 40 <= saved['print_width'] <= 240:
                width = saved['print_width']
            if saved.get('php_version') in ('7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5'):
                php_version = saved['php_version']
    except (OSError, ValueError):
        pass
    busy = False
    window = None

    def format_document():
        nonlocal busy
        if busy:
            return
        path = api.get_path()
        if not is_php(path):
            messagebox.showinfo('PHP files only', 'Open or save a PHP file before formatting.', parent=root)
            return
        source, generation = api.get_text(), editor.document_generation
        cursor = len(editor.buffer.get('1.0', 'insert'))
        view = editor.buffer.yview()[0]
        captured_width, captured_tabs, captured_version = width, editor.tab_width, php_version
        busy = True

        def worker():
            try:
                results.put((tool.format(source, path, cursor, captured_width, captured_tabs, captured_version), None))
            except Exception as error:
                results.put((None, str(error)))

        def poll():
            nonlocal busy
            if editor.closing:
                return
            try:
                formatted, error = results.get_nowait()
            except queue.Empty:
                root.after(60, poll)
                return
            busy = False
            if error:
                messagebox.showerror('PHP formatting failed', error, parent=root)
                return
            if editor.document_generation != generation or api.get_path() != path or api.get_text() != source:
                messagebox.showinfo('Document changed', 'Formatting result discarded because the document changed.', parent=root)
                return
            if formatted['formatted'] == source:
                messagebox.showinfo('PHP formatting', 'This PHP document is already formatted.', parent=root)
                return
            api.replace_text(formatted['formatted'])
            editor.buffer.mark_set('insert', f"1.0 +{max(0, formatted['cursorOffset'])}c")
            editor.buffer.yview_moveto(view)
            editor.refresh()
            api.emit('document_formatted', language='PHP')

        threading.Thread(target=worker, daemon=True).start()
        root.after(60, poll)

    def preferences():
        nonlocal window
        if window is not None and window.winfo_exists():
            window.lift(); return
        window = tk.Toplevel(root)
        window.title('PHP Formatter'); window.transient(root); window.resizable(False, False)
        body = ttk.Frame(window, padding=20); body.pack(fill='both', expand=True)
        columns = tk.StringVar(window, str(width)); version = tk.StringVar(window, php_version)
        ttk.Label(body, text='Line width').grid(row=0, column=0, sticky='w', pady=8, padx=(0, 16))
        ttk.Spinbox(body, from_=40, to=240, textvariable=columns, width=8).grid(row=0, column=1, sticky='w')
        ttk.Label(body, text='Target PHP version').grid(row=1, column=0, sticky='w', pady=8)
        ttk.Combobox(body, textvariable=version, values=('7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5'),
                     state='readonly', width=8).grid(row=1, column=1, sticky='w')
        ttk.Label(body, text='Formats the buffer, never the file on disk. Undo restores the original.\n'
                  'Project Prettier / EditorConfig settings override these defaults.').grid(
                      row=2, column=0, columnspan=2, sticky='w', pady=16)

        def save():
            nonlocal width, php_version
            try:
                selected = int(columns.get())
                if not 40 <= selected <= 240:
                    raise ValueError
            except ValueError:
                messagebox.showerror('Line width', 'Enter a number from 40 to 240.', parent=window); return
            try:
                editor.write_data(settings_path, json.dumps({'print_width': selected,
                    'php_version': version.get()}, indent=2).encode('utf-8'))
            except OSError as error:
                messagebox.showerror('PHP settings', str(error), parent=window); return
            width, php_version = selected, version.get()
            window.destroy()

        ttk.Button(body, text='Save Settings', command=save, style='Accent.TButton').grid(row=3, column=1, sticky='e')

    api.add_command('Format PHP', format_document)
    api.add_command('PHP Format Settings', preferences)
    api.provide_service('format', format_document)
    api.provide_service('busy', lambda: busy)
