"""Codex CLI in Pocket Editor; uses the user's own CLI authentication."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from pocket_editor_plugins._codex_cli import CodexRunner, cli_prefix
from pocket_editor_plugins._loading import begin, end


class CodexAssistant:
    def __init__(self, api):
        self.api, self.editor = api, api._editor
        self.root = self.editor.root
        self.runner, self.results = CodexRunner(), queue.Queue(maxsize=1)
        self.window, self.timer, self.loading = None, None, None
        self.busy, self.closed, self.last_reply = False, False, ''
        api.add_command('Codex Assistant…', self.show)
        api.provide_service('get_status', lambda: {'busy': self.busy})
        api.on_cleanup(self.cleanup)

    def project_folder(self):
        for service in ('project_explorer.get_project', 'git_project.get_repository'):
            try:
                folder = self.api.call_service(service)
                if folder: return str(folder)
            except LookupError: pass
        path = self.api.get_path()
        return str(path.parent if path else Path.cwd())

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify(); self.window.lift(); return
        self.window = window = tk.Toplevel(self.root)
        window.title('Codex Assistant'); window.geometry('900x760'); window.minsize(660, 560)
        window.protocol('WM_DELETE_WINDOW', window.withdraw)
        body = ttk.Frame(window, padding=18); body.pack(fill='both', expand=True)
        header = ttk.Frame(body); header.pack(fill='x')
        ttk.Label(header, text='Codex Assistant', style='Title.TLabel').pack(side='left')
        ttk.Button(header, text='How to use', command=self.help).pack(side='right')
        fields = ttk.Frame(body); fields.pack(fill='x', pady=10); fields.columnconfigure(1, weight=1)
        self.folder = tk.StringVar(window, self.project_folder())
        self.executable = tk.StringVar(window)
        self.model = tk.StringVar(window)
        for row, (label, variable) in enumerate((('Project folder', self.folder), ('Codex executable (auto if empty)', self.executable), ('Model (CLI default if empty)', self.model))):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky='w', pady=4, padx=(0, 10))
            ttk.Entry(fields, textvariable=variable).grid(row=row, column=1, sticky='ew', pady=4)
        ttk.Button(fields, text='Browse…', command=self.browse_folder).grid(row=0, column=2, padx=6)
        ttk.Button(fields, text='Browse…', command=self.browse_executable).grid(row=1, column=2, padx=6)
        controls = ttk.Frame(body); controls.pack(fill='x')
        ttk.Button(controls, text='Detect Codex', command=self.detect).pack(side='left')
        ttk.Button(controls, text='Copy install command', command=lambda: self.copy('npm install -g @openai/codex')).pack(side='left', padx=6)
        ttk.Button(controls, text='Copy login command', command=lambda: self.copy('codex login')).pack(side='left')
        self.attach, self.edits = tk.BooleanVar(window, False), tk.BooleanVar(window, False)
        ttk.Checkbutton(body, text='Attach current tab snapshot (includes unsaved edits)', variable=self.attach).pack(anchor='w', pady=(10, 2))
        ttk.Checkbutton(body, text='Allow Codex to edit saved files in this project', variable=self.edits).pack(anchor='w')
        ttk.Label(body, text='Read-only by default. Each Send is a separate CLI task using your Codex account and limits.', wraplength=800).pack(anchor='w', pady=6)
        self.output = ScrolledText(body, height=10, state='disabled', wrap='word', background='#1e2230', foreground='#dfe7ff', insertbackground='#dfe7ff')
        self.output.pack(fill='both', expand=True, pady=8)
        self.loading_area = ttk.Frame(body); self.loading_area.pack(fill='x')
        self.prompt = ScrolledText(body, height=4, wrap='word'); self.prompt.pack(fill='x')
        self.prompt.bind('<Control-Return>', lambda event: self.submit() or 'break')
        footer = ttk.Frame(body); footer.pack(fill='x', pady=8)
        self.send = ttk.Button(footer, text='Send to Codex', command=self.submit); self.send.pack(side='right')
        self.stop_button = ttk.Button(footer, text='Stop', command=self.stop, state='disabled'); self.stop_button.pack(side='right', padx=6)
        ttk.Button(footer, text='Open reply in new tab', command=self.open_reply).pack(side='left')
        self.status = tk.StringVar(window, 'Install/login once in a terminal, then Detect Codex. See How to use.')
        ttk.Label(body, textvariable=self.status, wraplength=800).pack(fill='x')
        self.timer = self.root.after(80, self.poll)

    def copy(self, text):
        self.root.clipboard_clear(); self.root.clipboard_append(text)
        self.status.set('Command copied. Paste and run it in your own terminal.')

    def browse_folder(self):
        selected = filedialog.askdirectory(parent=self.window)
        if selected: self.folder.set(selected)

    def browse_executable(self):
        selected = filedialog.askopenfilename(parent=self.window, title='Choose installed Codex executable or codex.cmd')
        if selected: self.executable.set(selected)

    def detect(self):
        try:
            prefix = cli_prefix(self.executable.get())
            self.status.set('Found Codex: ' + ' '.join(prefix) + '. Run codex login in a terminal if not signed in.')
        except RuntimeError as error: self.status.set(str(error))

    def help(self):
        window = tk.Toplevel(self.window); window.title('Using Codex in Pocket Editor'); window.geometry('740x600')
        text = ScrolledText(window, wrap='word', padx=16, pady=16); text.pack(fill='both', expand=True)
        guide = Path(__file__).resolve().parents[1] / 'docs/CODEX.md'
        text.insert('1.0', guide.read_text(encoding='utf-8') if guide.exists() else 'Install Codex CLI, run codex login in a terminal, choose a project and send a request. Read-only is the default.')
        text.configure(state='disabled')

    def submit(self):
        if self.busy: return
        prompt = self.prompt.get('1.0', 'end-1c').strip()
        folder = self.folder.get().strip()
        if not prompt or not folder or not Path(folder).is_dir():
            messagebox.showerror('Codex', 'Enter a request and choose an existing project folder.', parent=self.window); return
        if self.attach.get(): prompt += '\n\nCurrent tab snapshot:\n' + self.api.get_text()
        if len(prompt.encode('utf-8')) > 65536:
            messagebox.showerror('Codex', 'Prompt and tab exceed 64 KiB. Use a smaller request.', parent=self.window); return
        edits = self.edits.get()
        if edits and any(document.get('path') and Path(document['path']).resolve().is_relative_to(Path(folder).resolve()) for document in self.editor.documents):
            messagebox.showinfo('Close project tabs', 'Close project tabs before edit mode so on-disk changes cannot conflict with open buffers. Read-only mode can use an attached snapshot.', parent=self.window); return
        selected, model = self.executable.get().strip(), self.model.get().strip()
        self.runner.prepare()
        self.busy = True; self.send.configure(state='disabled'); self.stop_button.configure(state='normal')
        self.status.set('Codex is working…'); self.loading = begin(self.api, 'Codex is working…', parent=self.loading_area)
        def work():
            try: result = (self.runner.run(selected, folder, prompt, edits, model), None)
            except Exception as error: result = (None, str(error))
            if not self.closed: self.results.put_nowait(result)
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        self.timer = None
        if self.closed: return
        try: reply, error = self.results.get_nowait()
        except queue.Empty: pass
        else:
            end(self.api, self.loading); self.loading = None
            self.busy = False; self.send.configure(state='normal'); self.stop_button.configure(state='disabled')
            self.status.set(error or 'Codex finished. Review the reply and any project changes.')
            self.output.configure(state='normal'); self.output.insert('end', (error or reply) + '\n\n'); self.output.see('end'); self.output.configure(state='disabled')
            if reply: self.last_reply = reply
        self.timer = self.root.after(80, self.poll)

    def stop(self):
        if self.busy:
            self.status.set('Stopping Codex…')
            threading.Thread(target=self.runner.cancel, daemon=True).start()

    def open_reply(self):
        if self.last_reply: self.editor.new(); self.api.replace_text(self.last_reply)

    def cleanup(self):
        self.closed = True
        end(self.api, self.loading)
        if self.timer is not None: self.root.after_cancel(self.timer)
        self.runner.close()
        if self.window is not None and self.window.winfo_exists(): self.window.destroy()


def register(api):
    CodexAssistant(api)
