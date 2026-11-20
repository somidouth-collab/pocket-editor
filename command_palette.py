"""Search the editor's live menus without duplicating command registration."""
import tkinter as tk
from tkinter import ttk


def menu_commands(root, menu, prefix=''):
    commands = []
    last = menu.index('end')
    for index in range((last + 1) if last is not None else 0):
        kind = menu.type(index)
        if kind not in ('command', 'checkbutton', 'radiobutton', 'cascade'):
            continue
        if menu.entrycget(index, 'state') == 'disabled':
            continue
        label = menu.entrycget(index, 'label')
        title = f'{prefix} › {label}' if prefix else label
        if kind == 'cascade':
            child = root.nametowidget(menu.entrycget(index, 'menu'))
            commands.extend(menu_commands(root, child, title))
        else:
            shortcut = menu.entrycget(index, 'accelerator')
            commands.append((title, shortcut, lambda menu=menu, index=index: menu.invoke(index)))
    return commands


def filter_commands(commands, query):
    words = query.strip().casefold().split()
    matches = [command for command in commands if all(word in command[0].casefold() for word in words)]
    if words:
        phrase = ' '.join(words)
        matches.sort(key=lambda item: (not item[0].split(' › ')[-1].casefold().startswith(phrase), item[0].count(' › '), item[0].casefold()))
    return matches


class CommandPalette:
    def __init__(self, editor):
        self.editor = editor
        self.window = None

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            self.entry.focus_set()
            self.entry.selection_range(0, 'end')
            return
        root = self.editor.root
        self.commands = menu_commands(root, self.editor.menu_bar)
        self.commands.extend(menu_commands(root, self.editor.plugins, "Plugins"))
        window = self.window = tk.Toplevel(root)
        window.title('Command Palette')
        window.transient(root)
        window.configure(background='#11161c')
        window.resizable(True, True)
        window.minsize(340, 240)
        root.update_idletasks()
        width = min(720, max(340, root.winfo_width() - 48))
        window.geometry(f'{width}x380+{root.winfo_rootx() + (root.winfo_width() - width) // 2}+{root.winfo_rooty() + 40}')
        window.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(window, padding=12)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Run a command', style='Title.TLabel').pack(anchor='w', pady=(0, 8))
        self.query = tk.StringVar(window)
        self.entry = ttk.Entry(body, textvariable=self.query)
        self.entry.pack(fill='x', pady=(0, 8))
        panel = ttk.Frame(body)
        panel.pack(fill='both', expand=True)
        self.results = tk.Listbox(panel, background='#10151b', foreground='#d1dae4',
                                  selectbackground='#243f59', selectforeground='white',
                                  activestyle='none', highlightthickness=0, borderwidth=0,
                                  font=self.editor.ui_font, exportselection=False)
        scrollbar = ttk.Scrollbar(panel, command=self.results.yview)
        self.results.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.results.pack(fill='both', expand=True)
        self.hint = ttk.Label(body, style='Muted.TLabel')
        self.hint.pack(anchor='w', pady=(8, 0))
        self.query.trace_add('write', self.update)
        self.entry.bind('<Down>', lambda event: self.move(1))
        self.entry.bind('<Up>', lambda event: self.move(-1))
        window.bind('<Return>', self.run)
        window.bind('<Escape>', lambda event: self.close())
        self.results.bind('<Double-Button-1>', self.run)
        self.update()
        window.grab_set()
        self.entry.focus_set()

    def update(self, *args):
        self.matches = filter_commands(self.commands, self.query.get())
        self.results.delete(0, 'end')
        for title, shortcut, callback in self.matches:
            self.results.insert('end', title + (f'    {shortcut}' if shortcut else ''))
        if self.matches:
            self.results.selection_set(0)
            self.results.activate(0)
        self.hint.configure(text=f'{len(self.matches)} commands · ↑ ↓ navigate · Enter run · Esc close'
                            if self.matches else 'No matching commands. Try another search.')

    def move(self, delta):
        if self.matches:
            selection = self.results.curselection()
            index = max(0, min(len(self.matches) - 1, (selection[0] if selection else 0) + delta))
            self.results.selection_clear(0, 'end')
            self.results.selection_set(index)
            self.results.activate(index)
            self.results.see(index)
        return 'break'

    def run(self, event=None):
        selection = self.results.curselection()
        if selection:
            callback = self.matches[selection[0]][2]
            # Restore editor focus before commands such as Paste or tool dialogs run.
            self.close()
            callback()
        return 'break'

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = None
        if not self.editor.closing:
            self.editor.buffer.focus_set()
        return 'break'
