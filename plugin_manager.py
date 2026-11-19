"""Built-in plugin inspection and persistent startup controls."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk


class PluginManager:
    def __init__(self, editor, directory):
        self.editor, self.directory = editor, directory
        self.window = None
        self.paths = {}

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            self.refresh()
            return
        self.window = window = tk.Toplevel(self.editor.root)
        window.title('Plugin Manager')
        window.transient(self.editor.root)
        window.geometry('820x620')
        window.minsize(560, 400)
        body = ttk.Frame(window, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Plugin Manager', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='Disabling a loaded plugin takes effect after restart. Active commands remain available until then.',
                  wraplength=760, style='Muted.TLabel').pack(anchor='w', pady=(4, 10))
        self.query = tk.StringVar(window)
        ttk.Entry(body, textvariable=self.query).pack(fill='x', pady=(0, 8))
        panel = ttk.Frame(body)
        panel.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(panel, columns=('status',), show='tree headings', height=8, selectmode='browse')
        self.tree.heading('#0', text='Plugin')
        self.tree.heading('status', text='Status')
        self.tree.column('#0', width=230)
        self.tree.column('status', width=380)
        scrollbar = ttk.Scrollbar(panel, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        details_panel = ttk.Frame(body)
        details_panel.pack(fill='both', expand=True, pady=10)
        self.details = tk.Text(details_panel, background='#10151b', foreground='#d1dae4',
                               font=self.editor.ui_font, wrap='word', height=9, borderwidth=0,
                               padx=10, pady=8, state='disabled')
        details_scroll = ttk.Scrollbar(details_panel, command=self.details.yview)
        self.details.configure(yscrollcommand=details_scroll.set)
        details_scroll.pack(side='right', fill='y')
        self.details.pack(fill='both', expand=True)
        buttons = ttk.Frame(body)
        buttons.pack(fill='x')
        self.toggle_button = ttk.Button(buttons, text='Enable / Disable', command=self.toggle)
        self.toggle_button.pack(side='left')
        self.open_button = ttk.Button(buttons, text='Open source', command=self.open_source)
        self.open_button.pack(side='left', padx=8)
        ttk.Button(buttons, text='Refresh', command=self.refresh).pack(side='left')
        ttk.Button(buttons, text='Load new plugins', command=self.load_new,
                   state='disabled' if self.editor.safe_mode else 'normal').pack(side='right')
        self.query.trace_add('write', lambda *args: self.refresh())
        self.tree.bind('<<TreeviewSelect>>', self.inspect)
        window.bind('<Escape>', lambda event: window.destroy())
        self.refresh()

    def status(self, plugin_id, path):
        loaded = plugin_id in self.editor.plugin_apis
        disabled = plugin_id in self.editor.disabled_plugins
        if disabled:
            return 'Loaded · disable after restart' if loaded else 'Disabled'
        if not path.is_file():
            return 'Loaded · source missing' if loaded else 'Source missing'
        if self.editor.safe_mode:
            return 'Not loaded · safe mode'
        if plugin_id in self.editor.plugin_errors:
            return 'Loaded · error recorded' if loaded else 'Load failed'
        return 'Loaded' if loaded else 'Enabled · not loaded'

    def refresh(self):
        selection = self.tree.selection()
        selected = selection[0] if selection else None
        folder = Path(self.directory())
        self.paths = {path.stem: path for path in sorted(folder.glob('*.py')) if not path.name.startswith('_')}
        self.paths.update({path.stem: path for path in self.editor.loaded_plugins if path.stem not in self.paths})
        for plugin_id in self.editor.disabled_plugins:
            self.paths.setdefault(plugin_id, folder / (plugin_id + '.py'))
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().casefold().split()
        for plugin_id, path in sorted(self.paths.items()):
            state = self.status(plugin_id, path)
            if all(word in f'{plugin_id} {state}'.casefold() for word in query):
                self.tree.insert('', 'end', iid=plugin_id, text=plugin_id, values=(state,))
        rows = self.tree.get_children()
        if rows:
            self.tree.selection_set(selected if selected in rows else rows[0])
        self.inspect()

    def inspect(self, event=None):
        selection = self.tree.selection()
        plugin_id = selection[0] if selection else None
        self.toggle_button.configure(state='normal' if plugin_id else 'disabled',
                                     text='Enable' if plugin_id in self.editor.disabled_plugins else 'Disable')
        path = self.paths.get(plugin_id)
        self.open_button.configure(state='normal' if path and path.is_file() else 'disabled')
        text = 'Select a plugin to inspect its commands, event subscriptions, services, and latest error.'
        if plugin_id:
            api = self.editor.plugin_apis.get(plugin_id)
            commands = [label for label, callback in api._commands] if api else []
            events = sorted({name for name, callback in api._subscriptions}) if api else []
            services = [f'{plugin_id}.{name}' for name in sorted(api._services)] if api else []
            text = (f'{plugin_id}\n{path}\n{self.status(plugin_id, path)}\n\n'
                    'Commands:\n' + ('\n'.join(commands) or '(none registered)') +
                    '\n\nSubscribed events:\n' + ('\n'.join(events) or '(none registered)') +
                    '\n\nServices:\n' + ('\n'.join(services) or '(none registered)') +
                    '\n\nLatest error:\n' + self.editor.plugin_errors.get(plugin_id, '(none recorded)'))
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def toggle(self):
        selection = self.tree.selection()
        if not selection:
            return
        plugin_id = selection[0]
        previous = set(self.editor.disabled_plugins)
        if plugin_id in previous:
            self.editor.disabled_plugins.remove(plugin_id)
        else:
            self.editor.disabled_plugins.add(plugin_id)
        if not self.editor.save_settings():
            self.editor.disabled_plugins = previous
        self.refresh()

    def open_source(self):
        selection = self.tree.selection()
        if selection:
            self.editor.open_path(self.paths[selection[0]])

    def load_new(self):
        self.editor.load_plugins()
        self.refresh()
