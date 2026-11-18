"""Read-only publisher and contact information for Pocket Editor."""
import tkinter as tk
from tkinter import messagebox, ttk

FIELDS = (('name', 'Display name'), ('description', 'Description'), ('website', 'Website'),
          ('email', 'Email'), ('copyright', 'Copyright text'))


DEFAULT_DETAILS = {
    'name': 'Somi Douth',
    'description': 'A modern, lightweight editor for code, plugins, and AI-assisted workflows.',
    'website': '',
    'email': 'somidouth@gmail.com',
    'copyright': '',
}



class AboutDialog:
    def __init__(self, editor, path=None):
        self.editor = editor
        self.window = None
        self.details = dict(DEFAULT_DETAILS)

    def load(self):
        # Published author information comes from the application, not a local
        # editable profile or an old installation's About override file.
        self.details = dict(DEFAULT_DETAILS)

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        self.load()
        self.window = window = tk.Toplevel(self.editor.root)
        window.title('About Pocket Editor')
        window.transient(self.editor.root)
        window.geometry('580x360'); window.minsize(480, 300)
        body = ttk.Frame(window, padding=24); body.pack(fill='both', expand=True)
        ttk.Label(body, text='Pocket Editor', style='Title.TLabel').pack(anchor='w', pady=(0, 12))
        self.description = ttk.Label(body, wraplength=520)
        self.description.pack(anchor='w', pady=(0, 8))
        ttk.Label(body, text='Built with Python and Tkinter', style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        style = ttk.Style(window)
        style.configure('About.TEntry', padding=6)
        style.map('About.TEntry', fieldbackground=[('readonly', '#1e1e1e')], foreground=[('readonly', '#dddddd')])
        self.info = ttk.Frame(body); self.info.pack(fill='both', expand=True)
        actions = ttk.Frame(body); actions.pack(fill='x', pady=(12, 0))
        ttk.Button(actions, text='Copy details', command=self.copy).pack(side='left', padx=8)
        ttk.Button(actions, text='Close', command=window.destroy).pack(side='right')
        window.bind('<Escape>', lambda event: window.destroy())
        window.bind('<Configure>', lambda event: self.description.configure(wraplength=max(300, event.width - 48)) if event.widget is window else None)
        self.render()

    def render(self):
        self.description.configure(text=self.details['description'] or 'A portable editor for text, code, and custom tools.')
        for widget in self.info.winfo_children():
            widget.destroy()
        self.info.columnconfigure(1, weight=1)
        row = 0
        for key, label in (('name', 'Created by'), ('website', 'Website'), ('email', 'Email'), ('copyright', 'Copyright')):
            if self.details[key]:
                ttk.Label(self.info, text=label, style='Muted.TLabel').grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
                variable = tk.StringVar(self.window, self.details[key])
                entry = ttk.Entry(self.info, textvariable=variable, state='readonly', style='About.TEntry')
                entry.variable = variable
                entry.grid(row=row, column=1, sticky='ew', pady=4)
                row += 1
        if not row:
            ttk.Label(self.info, text='Publisher details are not configured.', style='Muted.TLabel', wraplength=450).grid(sticky='w')

    def copy(self):
        lines = ['Pocket Editor'] + [self.details[key] for key, label in FIELDS if self.details[key]] + ['Built with Python and Tkinter']
        try:
            self.editor.root.clipboard_clear()
            self.editor.root.clipboard_append('\n'.join(lines))
        except tk.TclError:
            messagebox.showerror('About', 'Could not access the clipboard.', parent=self.window)

