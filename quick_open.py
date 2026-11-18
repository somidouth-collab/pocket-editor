"""Cancelable background project file discovery for Ctrl+P."""
import os
from pathlib import Path
import queue
import threading
from tkinter import filedialog

from command_palette import CommandPalette

SKIP_DIRECTORIES = {'.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv', 'venv', 'vendor'}


def scan_files(folder, canceled, output, limit=50000, directory_limit=20000):
    def send(item):
        while not canceled.is_set():
            try:
                output.put(item, timeout=.1)
                return True
            except queue.Full:
                pass
        return False

    count = directories = 0
    batch = []
    errors = []
    limited = False
    for directory, children, names in os.walk(folder, followlinks=False, onerror=lambda error: errors.append(str(error))):
        if canceled.is_set():
            return
        directories += 1
        if directories > directory_limit:
            limited = True
            break
        children[:] = sorted(name for name in children if name not in SKIP_DIRECTORIES and
                             not Path(directory, name).is_symlink())
        for name in sorted(names):
            if canceled.is_set():
                return
            if count >= limit:
                limited = True
                break
            path = Path(directory, name)
            if not path.is_file():
                continue
            batch.append(path)
            count += 1
            if len(batch) == 100:
                if not send(('files', batch)):
                    return
                batch = []
        if limited:
            break
    if batch and not send(('files', batch)):
        return
    send(('done', (limited, bool(errors))))


class QuickOpen(CommandPalette):
    def __init__(self, editor):
        super().__init__(editor)
        self.poll_job = None
        self.canceled = None
        self.folder = None
        self.scanning = False
        self.note = ''

    def project_folder(self):
        provider = self.editor.plugin_apis.get('project_explorer')
        if provider and 'get_project' in provider._services:
            folder = provider.call_service('project_explorer.get_project')
            if folder:
                return Path(folder)
        if self.editor.path:
            return self.editor.path.parent
        if self.folder and self.folder.is_dir():
            return self.folder
        folder = filedialog.askdirectory(parent=self.editor.root, title='Quick Open — choose a project folder')
        return Path(folder) if folder else None

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            super().show()
            return
        folder = self.project_folder()
        if folder is None:
            return
        self.folder = folder
        self.scanning, self.note = True, ''
        super().show()
        self.window.title('Quick Open')
        self.window.winfo_children()[0].winfo_children()[0].configure(text=f'Open a file · {folder.name or folder}')
        self.commands = []
        self.update()
        self.canceled = threading.Event()
        self.output = queue.Queue(maxsize=8)
        threading.Thread(target=scan_files, args=(folder, self.canceled, self.output), daemon=True).start()
        self.poll_job = self.editor.root.after(30, self.poll)

    def update(self, *args):
        # The first base-class update happens before the file index replaces menus.
        if self.window is None:
            return
        from command_palette import filter_commands
        matches = filter_commands(self.commands if hasattr(self, 'commands') else [], self.query.get())
        self.matches = matches[:200]
        self.results.delete(0, 'end')
        for title, shortcut, callback in self.matches:
            self.results.insert('end', title)
        if self.matches:
            self.results.selection_set(0)
            self.results.activate(0)
        state = 'Scanning… · ' if self.scanning else ''
        self.hint.configure(text=f'{state}{len(matches)} files · showing up to 200 · Enter open · Esc cancel{self.note}')

    def poll(self):
        self.poll_job = None
        if self.window is None:
            return
        changed = False
        for _ in range(10):
            try:
                kind, value = self.output.get_nowait()
            except queue.Empty:
                break
            changed = True
            if kind == 'files':
                self.commands.extend((path.relative_to(self.folder).as_posix(), '',
                                      lambda path=path: self.editor.open_path(path)) for path in value)
            else:
                self.scanning = False
                limited, errors = value
                self.note = (' · Index limit reached; choose a smaller folder' if limited else '') + (' · Some folders were unreadable' if errors else '')
        if changed:
            self.update()
        if self.scanning:
            self.poll_job = self.editor.root.after(50, self.poll)

    def close(self):
        if self.canceled:
            self.canceled.set()
        if self.poll_job:
            self.editor.root.after_cancel(self.poll_job)
            self.poll_job = None
        return super().close()
