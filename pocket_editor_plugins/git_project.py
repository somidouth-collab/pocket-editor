"""Standalone Git tools for any local project; optional Project Explorer integration."""
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pocket_editor_plugins._git_settings import FIELDS, CHOICES, read_settings, save_settings, normalize


class GitRunner:
    """One cancellable Git command at a time; never invokes a shell."""
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.closed = False

    def run(self, folder, arguments, timeout=120):
        environment = os.environ.copy()
        environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never",
                           GIT_EDITOR="true", GIT_MERGE_AUTOEDIT="no")
        environment.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oConnectTimeout=15")
        with self.lock:
            if self.closed:
                raise RuntimeError("Team Project has stopped.")
            if self.process is not None:
                raise RuntimeError("Another Git operation is running.")
            process = subprocess.Popen(
                ["git", "-C", str(folder), *arguments], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
            self.process = process
        try:
            try:
                output, errors = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise RuntimeError("Git timed out. Check the connection and authentication.")
            if process.returncode:
                detail = (errors or output).decode("utf-8", "replace")[-16000:]
                raise RuntimeError(detail.strip() or f"Git exited with code {process.returncode}.")
            return output, errors
        finally:
            with self.lock:
                self.process = None

    def close(self):
        with self.lock:
            self.closed = True
            if self.process is not None:
                self.process.terminate()


def changed_paths(data):
    """Parse porcelain v1 NUL records, including the second path of renames."""
    records = data.split(b"\0")
    rows = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise ValueError("Unexpected Git status output.")
        state = record[:2].decode("ascii")
        path = os.fsdecode(record[3:])
        paths = [path]
        if "R" in state or "C" in state:
            if index >= len(records) or not records[index]:
                raise ValueError("Incomplete Git rename record.")
            paths.append(os.fsdecode(records[index]))
            index += 1
        rows.append((state, path, paths))
    return rows


def register(api):
    GitProject(api)


class GitProject:
    def __init__(self, api):
        self.api = api
        self.editor = api._editor
        self.root = self.editor.root
        self.window = None
        self.runner = GitRunner()
        self.results = queue.Queue()
        self.busy = False
        self.closed = False
        self.job = None
        self.generation = 0
        self.rows = {}
        self.reviewed = None
        self.buttons = []
        self.repository = None
        self.config_window = None
        self.config_widgets = []
        self.config_vars = {}
        self.config_repo = None
        self.config_loaded = False
        api.add_command("Git Project", self.show)
        api.provide_service("open_project", self.open_project)
        api.provide_service("ensure_repository", self.ensure_repository)
        api.provide_service("get_repository", self.get_repository)
        api.provide_service("get_project", self.get_repository)
        api.on_cleanup(self.cleanup)

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return
        self.window = tk.Toplevel(self.root)
        self.window.title("Git Project")
        self.window.geometry("780x650")
        self.window.minsize(620, 520)
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        body = ttk.Frame(self.window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Git Project", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="Select a folder to prepare Git and share its repository with other plugins.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 12))
        self.folder = tk.StringVar(value=self.project_folder())
        location = ttk.Frame(body)
        location.pack(fill="x")
        ttk.Label(location, text="Project").pack(side="left", padx=(0, 8))
        self.folder_entry = ttk.Entry(location, textvariable=self.folder)
        self.folder_entry.pack(side="left", fill="x", expand=True)
        self.button(location, "Browse", self.browse).pack(side="left", padx=(6, 0))
        self.button(location, "Use Explorer", self.use_explorer).pack(side="left", padx=(6, 0))
        self.remote = tk.StringVar()
        remote_row = ttk.Frame(body)
        remote_row.pack(fill="x", pady=(8, 12))
        ttk.Label(remote_row, text="Remote URL").pack(side="left", padx=(0, 8))
        self.remote_entry = ttk.Entry(remote_row, textvariable=self.remote)
        self.remote_entry.pack(side="left", fill="x", expand=True)
        self.button(remote_row, "Clone…", self.clone).pack(side="left", padx=(6, 0))
        self.button(remote_row, "Set Origin", self.set_origin).pack(side="left", padx=(6, 0))
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(0, 10))
        for label, callback in (("Refresh", self.refresh), ("Set Git Folder", self.init_repository), ("Pull", self.pull), ("Push", self.push), ("Settings…", self.show_settings)):
            self.button(actions, label, callback).pack(side="left", padx=(0, 6))
        self.status = tk.StringVar(value="Choose a repository or clone your team's URL.")
        ttk.Label(body, textvariable=self.status, wraplength=700).pack(anchor="w", pady=(0, 8))
        table = ttk.Frame(body)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("state", "path"), show="headings",
                                 selectmode="extended", height=8)
        self.tree.heading("state", text="Change")
        self.tree.heading("path", text="File — select changes to commit")
        self.tree.column("state", width=70, stretch=False)
        self.tree.column("path", width=450)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        commit_row = ttk.Frame(body)
        commit_row.pack(fill="x", pady=10)
        self.message = tk.StringVar()
        self.message_entry = ttk.Entry(commit_row, textvariable=self.message)
        self.message_entry.pack(side="left", fill="x", expand=True)
        self.button(commit_row, "Commit Selected", self.commit).pack(side="left", padx=(6, 0))
        ttk.Label(body, text="Save your edits before committing. Pull requires project tabs to be closed.\n"
                  "Use your existing Git SSH key or credential manager. Pull only fast-forwards; Push never forces.",
                  style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(0, 8))
        self.output = tk.Text(body, height=6, wrap="word", state="disabled")
        self.output.pack(fill="x")
        self.folder.trace_add("write", lambda *_: self.invalidate())
        self.job = self.root.after(80, self.poll)
        if self.folder.get():
            self.refresh()

    def get_repository(self):
        """Return only a prepared working-tree root, independently of the GUI."""
        if self.repository is not None:
            if not self.repository.is_dir() or not (self.repository / '.git').exists():
                self.repository = None
        return self.repository

    def ensure_repository(self, folder=None):
        """Local-only setup service: initialize if absent and return the Git root."""
        if self.closed:
            raise ValueError("Git Project has stopped.")
        if self.busy:
            raise ValueError("Wait for the current Git operation to finish.")
        if folder is None:
            folder = self.folder.get() if self.window is not None else (self.get_repository() or self.project_folder())
        if not folder or not str(folder).strip():
            raise ValueError("Choose an existing project folder first.")
        folder = Path(folder).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("Choose an existing project folder first.")
        initialized = False
        try:
            output, _ = self.runner.run(folder, ['rev-parse', '--show-toplevel'], timeout=10)
        except RuntimeError:
            # Existing damaged metadata must report its error, not be replaced.
            if any((parent / '.git').exists() or (parent / '.git').is_symlink()
                   for parent in (folder, *folder.parents)):
                raise
            try:
                bare, _ = self.runner.run(folder, ['rev-parse', '--is-bare-repository'], timeout=10)
            except RuntimeError:
                bare = b'false'
            if bare.strip() == b'true':
                raise ValueError("Choose a project working folder rather than a bare Git repository.")
            self.runner.run(folder, ['init'], timeout=10)
            output, _ = self.runner.run(folder, ['rev-parse', '--show-toplevel'], timeout=10)
            initialized = True
        repository = Path(os.fsdecode(output).strip()).resolve()
        if not repository.is_dir() or not (repository / '.git').exists():
            raise ValueError("Git did not return a valid project working folder.")
        changed = repository != self.repository
        self.repository = repository
        if self.window is not None and self.folder.get() != str(repository):
            self.folder.set(str(repository))
        if changed or initialized:
            self.api.emit('git_project.repository_changed', path=str(repository), initialized=initialized)
        if initialized:
            self.api.emit('project_changed', action='git_initialized', path=str(repository))
        return repository

    def open_project(self, folder):
        repository = self.ensure_repository(folder)
        if self.window is None:
            self.show()
        else:
            self.show()
            self.refresh()
        return repository

    def init_repository(self):
        if self.busy:
            return
        try:
            self.ensure_repository()
        except (OSError, RuntimeError, ValueError) as error:
            self.report_error(error)
            return
        self.refresh()

    def button(self, parent, label, callback):
        button = ttk.Button(parent, text=label, command=callback)
        self.buttons.append(button)
        return button

    def project_folder(self):
        if self.get_repository() is not None:
            return str(self.repository)
        try:
            return str(self.api.call_service("project_explorer.get_project") or "")
        except LookupError:
            return ""

    def invalidate(self):
        self.close_settings()
        self.generation += 1
        self.reviewed = None
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())

    def use_explorer(self):
        try:
            folder = self.api.call_service('project_explorer.get_project')
            if not folder:
                raise ValueError('Choose a folder in Project Explorer first.')
            self.open_project(folder)
        except (LookupError, OSError, RuntimeError, ValueError) as error:
            self.report_error(error)

    def browse(self):
        folder = filedialog.askdirectory(parent=self.window, title="Git Project Folder")
        if folder:
            try:
                self.open_project(folder)
            except (OSError, RuntimeError, ValueError) as error:
                self.report_error(error)

    def directory(self):
        value = self.folder.get().strip()
        if not value or not Path(value).expanduser().is_dir():
            raise ValueError("Choose an existing project folder first.")
        folder = Path(value).expanduser().resolve()
        if not (folder / ".git").exists():
            raise ValueError("Choose the root folder of a Git clone, or clone a repository first.")
        return folder

    def log(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text[-16000:] + "\n")
        if int(self.output.index("end-1c").split(".")[0]) > 300:
            self.output.delete("1.0", "end-250l")
        self.output.see("end")
        self.output.configure(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        for widget in self.config_widgets:
            if widget.winfo_exists():
                widget.configure(state='disabled' if busy or (widget is getattr(self, 'config_save', None) and not self.config_loaded) else ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
        for widget in [*self.buttons, self.folder_entry, self.remote_entry, self.message_entry, self.tree]:
            if isinstance(widget, ttk.Treeview):
                widget.state(["disabled"] if busy else ["!disabled"])
            else:
                widget.configure(state="disabled" if busy else "normal")

    def start(self, title, task, done=None):
        if self.busy or self.closed:
            return
        self.set_busy(True)
        self.status.set(title + "…")
        generation = self.generation
        def work():
            try:
                result = task()
                self.results.put((generation, title, result, None, done))
            except Exception as error:
                self.results.put((generation, title, None, str(error), done))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        self.job = None
        if self.closed:
            return
        try:
            while True:
                generation, title, result, error, done = self.results.get_nowait()
                self.set_busy(False)
                if error:
                    self.status.set(title + " failed. See details below.")
                    self.log(error)
                elif generation == self.generation:
                    self.status.set(title + " complete.")
                    if done:
                        try:
                            done(result)
                        except (OSError, ValueError, tk.TclError) as error:
                            self.status.set(title + " completed, but the view could not refresh.")
                            self.log(str(error))
        except queue.Empty:
            pass
        self.job = self.root.after(80, self.poll)

    def report_error(self, error):
        messagebox.showerror("Git Project", str(error), parent=self.window)

    def refresh(self):
        if self.busy:
            return
        try:
            folder = self.ensure_repository(self.folder.get())
        except (OSError, RuntimeError, ValueError) as error:
            self.report_error(error)
            return
        self.reviewed = None
        def task():
            top, _ = self.runner.run(folder, ["rev-parse", "--show-toplevel"])
            if Path(os.fsdecode(top).strip()).resolve() != folder:
                raise ValueError("Choose the repository root folder, not a subfolder.")
            branch, _ = self.runner.run(folder, ["status", "--short", "--branch", "--untracked-files=no"])
            files, _ = self.runner.run(folder, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
            return branch.decode("utf-8", "replace").splitlines()[0], changed_paths(files)
        def done(result):
            branch, rows = result
            self.tree.delete(*self.tree.get_children())
            self.rows.clear()
            for state, path, paths in rows:
                item = self.tree.insert("", "end", values=(state, path))
                self.rows[item] = paths
            self.reviewed = folder
            self.status.set(f"{branch} · {len(rows)} changed files")
        self.start("Refresh", task, done)

    def show_settings(self):
        if self.busy:
            return
        try:
            folder = self.ensure_repository(self.folder.get())
        except (OSError, RuntimeError, ValueError) as error:
            self.report_error(error)
            return
        if self.config_window is not None and self.config_window.winfo_exists():
            self.config_window.lift()
            return
        self.config_repo = folder
        self.config_loaded = False
        self.config_window = tk.Toplevel(self.window)
        self.config_window.title('Git Repository Settings')
        self.config_window.geometry('650x520')
        self.config_window.minsize(600, 480)
        self.config_window.protocol('WM_DELETE_WINDOW', self.close_settings)
        body = ttk.Frame(self.config_window, padding=18)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Repository Settings', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(body, text=str(folder), wraplength=590, style='Muted.TLabel').pack(anchor='w', pady=(4, 12))
        grid = ttk.Frame(body)
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)
        self.config_vars = {}
        self.config_widgets = []
        labels = ('Author name', 'Author email', 'Origin URL', 'Line endings (autocrlf)', 'Track executable bit', 'Sign commits (GPG)')
        for row, (key, label) in enumerate(zip(FIELDS, labels)):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=7)
            variable = tk.StringVar()
            self.config_vars[key] = variable
            if key in CHOICES:
                variable.set('Inherit')
                widget = ttk.Combobox(grid, textvariable=variable, values=('Inherit', *CHOICES[key][1:]), state='readonly')
            else:
                widget = ttk.Entry(grid, textvariable=variable)
            widget.grid(row=row, column=1, sticky='ew', pady=7)
            self.config_widgets.append(widget)
        self.config_hint = tk.StringVar(value='Loading repository settings…')
        ttk.Label(body, textvariable=self.config_hint, wraplength=590, style='Muted.TLabel').pack(anchor='w', pady=(10, 8))
        ttk.Label(body, text='Saved in this repository. Blank fields and Inherit remove a local override.\n'
                  'Git then uses inherited settings or its defaults. Authentication uses your SSH key or credential manager.',
                  wraplength=590).pack(anchor='w', pady=(0, 12))
        actions = ttk.Frame(body)
        actions.pack(side='bottom', fill='x')
        save = ttk.Button(actions, text='Save Settings', command=self.save_repository_settings, state='disabled')
        self.config_save = save
        save.pack(side='right')
        reload = ttk.Button(actions, text='Reload', command=self.load_repository_settings)
        reload.pack(side='right', padx=6)
        self.config_widgets.extend((save, reload))
        ttk.Button(actions, text='Close', command=self.close_settings).pack(side='left')
        self.load_repository_settings()

    def close_settings(self):
        if self.config_window is not None and self.config_window.winfo_exists():
            self.config_window.destroy()
        self.config_window = None
        self.config_widgets.clear()
        self.config_vars.clear()
        self.config_repo = None
        self.config_loaded = False
        for name in ('config_hint', 'config_save'):
            if hasattr(self, name):
                delattr(self, name)

    def display_settings(self, values, folder):
        if self.config_window is None or self.config_repo != folder:
            return
        for key, variable in self.config_vars.items():
            value = values['local'].get(key, '')
            variable.set(value if value or key not in CHOICES else 'Inherit')
        self.config_loaded = True
        self.config_save.configure(state='normal')
        effective = values['effective']
        self.config_hint.set('Effective author: ' + effective.get('user.name', '(not set)') + ' <' + effective.get('user.email', 'not set') + '>')
        self.remote.set(effective.get('remote.origin.url', ''))

    def load_repository_settings(self):
        if self.busy or self.config_repo is None:
            return
        folder = self.config_repo
        self.start('Load Git settings', lambda: read_settings(self.runner, folder),
                   lambda values: self.display_settings(values, folder))

    def save_repository_settings(self):
        if self.busy or self.config_repo is None or not self.config_loaded:
            return
        try:
            values = normalize({key: '' if variable.get() == 'Inherit' and key in CHOICES else variable.get()
                                for key, variable in self.config_vars.items()})
        except ValueError as error:
            self.report_error(error)
            return
        folder = self.config_repo
        def done(result):
            self.display_settings(result, folder)
            if self.config_window is not None:
                self.config_hint.set('Saved for this repository. ' + self.config_hint.get())
        self.start('Save Git settings', lambda: save_settings(self.runner, folder, values), done)

    def set_origin(self):
        if self.busy:
            return
        try:
            folder = self.directory()
            url = self.remote.get().strip()
            if not url or url.startswith('-') or any(c in url for c in '\r\n\0'):
                raise ValueError("Enter a valid repository URL first.")
        except ValueError as error:
            self.report_error(error)
            return
        def task():
            try:
                self.runner.run(folder, ['remote', 'get-url', 'origin'])
                command = 'set-url'
            except RuntimeError:
                command = 'add'
            return self.runner.run(folder, ['remote', command, 'origin', url])
        def done(result):
            self.log('Origin configured for this repository.')
            self.refresh()
        self.start('Set origin', task, done)

    def clone(self):
        if self.busy:
            return
        url = self.remote.get().strip()
        if not url or url.startswith("-") or "\n" in url:
            self.report_error("Enter your team's repository URL.")
            return
        parent = filedialog.askdirectory(parent=self.window, title="Clone into this parent folder")
        if not parent:
            return
        from tkinter import simpledialog
        name = simpledialog.askstring("Clone Project", "New project folder name", parent=self.window)
        if name is None:
            return
        if not name.strip() or name in (".", "..") or any(c in name for c in "/\\\0"):
            self.report_error("Enter a single new folder name.")
            return
        target = Path(parent).resolve() / name
        if target.exists():
            self.report_error("The destination already exists. Choose a new folder name.")
            return
        def done(result):
            self.folder.set(str(target))
            self.log("Cloned project to " + str(target))
            try:
                self.api.call_service("project_explorer.open_project", target)
            except LookupError:
                pass
            self.refresh()
        self.start("Clone", lambda: self.runner.run(parent, ["clone", "--", url, str(target)]), done)

    def commit(self):
        if self.busy:
            return
        try:
            folder = self.directory()
            if self.reviewed != folder:
                raise ValueError("Refresh this repository and select the files to commit.")
            selected = self.tree.selection()
            if not selected:
                raise ValueError("Select the changed files you want to commit.")
            message = self.message.get().strip()
            if not message:
                raise ValueError("Enter a commit message.")
            paths = list(dict.fromkeys(path for item in selected for path in self.rows[item]))
            self.editor.remember_document()
            for document in self.editor.documents:
                if document["path"] and document["path"].resolve().is_relative_to(folder):
                    if document["widget"].edit_modified() or document["rich_dirty"]:
                        raise ValueError("Save project edits before committing.")
        except ValueError as error:
            self.report_error(error)
            return
        def task():
            # Literal pathspecs handle filenames containing glob characters or colons.
            self.runner.run(folder, ["--literal-pathspecs", "add", "--", *paths])
            return self.runner.run(folder, ["--literal-pathspecs", "commit", "--only", "-m", message, "--", *paths])
        def done(result):
            self.message.set("")
            self.log((result[0] + result[1]).decode("utf-8", "replace"))
            self.refresh()
        self.start("Commit", task, done)

    def pull(self):
        if self.busy:
            return
        try:
            folder = self.directory()
            self.editor.remember_document()
            if any(document["path"] and document["path"].resolve().is_relative_to(folder)
                   for document in self.editor.documents):
                raise ValueError("Close this project's file tabs before pulling, so open buffers cannot overwrite incoming changes.")
        except ValueError as error:
            self.report_error(error)
            return
        def task():
            status, _ = self.runner.run(folder, ["status", "--porcelain=v1", "-z"])
            if status:
                raise ValueError("Commit or move local changes before pulling.")
            return self.runner.run(folder, ["pull", "--ff-only", "--no-rebase"])
        def done(result):
            self.log((result[0] + result[1]).decode("utf-8", "replace"))
            self.api.emit("project_changed", action="pulled", path=str(folder))
            self.refresh()
        self.start("Pull", task, done)

    def push(self):
        if self.busy:
            return
        try:
            folder = self.directory()
        except ValueError as error:
            self.report_error(error)
            return
        def done(result):
            self.log((result[0] + result[1]).decode("utf-8", "replace") or "Push complete.")
            self.refresh()
        def task():
            try:
                self.runner.run(folder, ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])
            except RuntimeError:
                self.runner.run(folder, ['symbolic-ref', '--quiet', '--short', 'HEAD'])
                return self.runner.run(folder, ['push', '--set-upstream', 'origin', 'HEAD'])
            return self.runner.run(folder, ['push'])
        self.start("Push", task, done)

    def cleanup(self):
        self.close_settings()
        self.closed = True
        self.runner.close()
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
