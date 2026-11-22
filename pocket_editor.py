"""Portable UTF-8 editor. Run with Python 3.9+ and Tkinter.

Plugins live in pocket_editor_plugins beside this script.
Each .py file defines register(api), then calls api.add_command(label, callback).
Callbacks can call api.get_text(), api.replace_text(text), and api.get_path().
Use --safe-mode to start without loading plugins.
"""

import argparse
import codecs
import json
import os
import stat
import sys
import tempfile
import runpy
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, simpledialog, ttk

from command_palette import CommandPalette
from quick_open import QuickOpen
from plugin_manager import PluginManager
from split_editor import SplitEditor
from editor_about import AboutDialog
from plugin_sidebar import PluginSidebar, DEFAULT_PINS

PLUGIN_DIR = Path(__file__).resolve().parent / "pocket_editor_plugins"
SETTINGS_PATH = PLUGIN_DIR.parent / "pocket_editor_settings.json"
DEFAULT_SETTINGS = {"line_numbers": True, "word_wrap": False, "font_size": 11, "font_family": "", "tab_width": 4, "input_method": "auto", "disabled_plugins": [], "sidebar_plugins": list(DEFAULT_PINS), "sidebar_active": "project_explorer", "sidebar_width": 320}
EXAMPLE = '''"""Add a command that counts words in the current document."""
from tkinter import messagebox


def register(api):
    def count_words():
        count = len(api.get_text().split())
        messagebox.showinfo("Word count", f"{count:,} words")

    api.add_command("Count words", count_words)
'''


def atomic_write(path, data):
    """Replace a complete file only after writing and syncing its new contents.

    Follow an existing symlink so saving does not replace the link itself.
    """
    path = Path(path).absolute().resolve()
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_root(input_method='auto'):
    """Select Tk's XIM connection before opening the display.

    Tk 8.6 with the external IBus/Fcitx XIM bridge can repeatedly fabricate key
    events. Automatic mode uses Xlib's local compose backend for this combination.
    Restore the environment immediately so launched project tools retain their
    normal desktop IME. System mode remains available for external composition.
    """
    if input_method not in ('auto','system','local'):
        raise ValueError('Input method must be auto, system, or local.')
    original=os.environ.get('XMODIFIERS')
    affected=(sys.platform.startswith('linux') and tk.TkVersion<9 and
              any(name in (original or '').lower() for name in ('@im=ibus','@im=fcitx')))
    local=sys.platform.startswith('linux') and (input_method=='local' or input_method=='auto' and affected)
    if local:os.environ['XMODIFIERS']='@im=none'
    try:root=tk.Tk()
    finally:
        if local:
            if original is None:os.environ.pop('XMODIFIERS',None)
            else:os.environ['XMODIFIERS']=original
    root.pocket_input_method='local' if local else 'system'
    return root


class DocumentText(tk.Text):
    """Observe successful edits without moving native Tcl errors into Python.

    Tk's Windows clipboard/selection bindings intentionally catch Tcl errors.
    Forwarding those commands through a Python createcommand callback leaves a
    pending Python exception even when the outer Tcl script caught the error.
    A Tcl alias preserves Tcl completion codes; Python only receives edit notices.
    """

    _DISPATCH = "::pocket_editor::text_dispatch"
    _BODY = r'''
        set operation [lindex $args 0]
        set editOperation [lindex $args 1]
        if {[llength $args] == 2 && $operation eq "edit" && $editOperation in {undo redo}} {
            set capability [expr {$editOperation eq "undo" ? "canundo" : "canredo"}]
            if {![$original edit $capability]} {return ""}
        }
        set result [$original {*}$args]
        if {$operation in {insert delete replace} || ($operation eq "edit" && $editOperation in {undo redo})} {
            $notify
        }
        return $result
    '''

    def __init__(self, master, changed, **options):
        super().__init__(master, **options)
        self._changed = changed
        self._original = self._w + "_original"
        self.tk.call("rename", self._w, self._original)
        self._observer = self._register(self._notify_change)
        self.tk.call("namespace", "eval", "::pocket_editor", "")
        if not self.tk.call("info", "procs", self._DISPATCH):
            self.tk.call("proc", self._DISPATCH, ("original", "notify", "args"), self._BODY)
        # Argument binding is done with Tcl lists, never interpolated script text.
        self.tk.call("interp", "alias", "", self._w, "", self._DISPATCH, self._original, self._observer)

    def _notify_change(self):
        self._changed()

    def destroy(self):
        super().destroy()
        if self.tk.call("interp", "alias", "", self._w):
            self.tk.call("rename", self._w, "")


class ActiveBuffer:
    """Stable plugin reference forwarding to the selected document's Tk widget."""

    def __init__(self, root, widget):
        self.root, self.widget = root, widget
        self.binding_tag = f"PocketBuffer{id(self)}"
        self.attach(widget)

    def attach(self, widget):
        self.widget = widget
        widget.bindtags((str(widget), self.binding_tag, "Text", str(self.root), "all"))

    def __getattr__(self, name):
        return getattr(self.widget, name)

    def __str__(self):
        return str(self.widget)

    def bind(self, sequence=None, func=None, add=None):
        if func is None:
            return self.root.bind_class(self.binding_tag, sequence)
        def active_only(event):
            if event.widget is self.widget:
                return func(event)
        return self.root.bind_class(self.binding_tag, sequence, active_only, add)

    def unbind(self, sequence, funcid=None):
        self.root._unbind(("bind", self.binding_tag, sequence), funcid)


class PluginAPI:
    """Commands are registered during loading and installed after registration."""

    def __init__(self, editor, plugin_id="anonymous"):
        self._editor = editor
        self.plugin_id = plugin_id
        self._subscriptions = []
        self._services = {}
        self._commands = []
        self._cleanups = []
        self._panel_factory = None
        self._panel_width = 360

    def on_cleanup(self, callback):
        if not callable(callback):
            raise ValueError("Provide a cleanup callable")
        self._cleanups.append(callback)

    def list_services(self):
        return tuple(sorted(f"{owner}.{name}" for owner, api in
                            self._editor.plugin_apis.items() for name in api._services))

    def list_plugins(self):
        """Return loaded plugin IDs (filename stems). Call on the GUI thread."""
        return tuple(sorted(path.stem for path in self._editor.loaded_plugins))

    def on(self, event, callback):
        """Subscribe on the GUI thread. Returns an unsubscribe function."""
        if not isinstance(event, str) or not event or not callable(callback):
            raise ValueError("Provide an event name and callable")
        subscription = (event, callback)
        self._subscriptions.append(subscription)

        def unsubscribe():
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

        return unsubscribe

    def emit(self, event, **payload):
        """Queue an event for all loaded plugins. Call on the GUI thread."""
        if not isinstance(event, str) or not event or "source" in payload:
            raise ValueError("Provide an event name; source is reserved")
        if not self._editor.closing:
            self._editor.root.after_idle(
                lambda: self._editor.dispatch_plugin_event(event, self.plugin_id, payload))

    def provide_service(self, name, callback):
        """Expose a callable under 'plugin_id.name'; available after loading."""
        if not isinstance(name, str) or not name or "." in name or not callable(callback):
            raise ValueError("Provide a simple service name and callable")
        if name in self._services:
            raise ValueError("Service already registered")
        self._services[name] = callback

    def call_service(self, qualified_name, *args, **kwargs):
        """Call a loaded service synchronously on the GUI thread."""
        owner, separator, name = qualified_name.partition(".")
        provider = self._editor.plugin_apis.get(owner)
        if not separator or provider is None or name not in provider._services:
            raise LookupError(f"Service unavailable: {qualified_name}")
        return provider._services[name](*args, **kwargs)

    def add_panel(self, factory, width=360):
        """Register a GUI-thread factory that creates this plugin's docked panel."""
        if not callable(factory) or not 180 <= width <= 900:
            raise ValueError("Provide a panel factory and a width between 180 and 900.")
        self._panel_factory, self._panel_width = factory, width

    def add_command(self, label, callback):
        if not isinstance(label, str) or not label.strip() or not callable(callback):
            raise ValueError("Provide a command label and a callable")
        self._commands.append((label, callback))

    def get_text(self):
        return self._editor.buffer.get("1.0", "end-1c")

    def replace_text(self, text):
        if not isinstance(text, str):
            raise TypeError("Expected a string")
        buffer = self._editor.buffer
        buffer.edit_separator()
        buffer.replace("1.0", "end-1c", text)
        buffer.edit_separator()

    def get_path(self):
        """Return the current pathlib.Path, or None for an untitled file."""
        return self._editor.path


def read_settings():
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_SETTINGS.copy()
    if not isinstance(saved, dict):
        return DEFAULT_SETTINGS.copy()
    settings = DEFAULT_SETTINGS.copy()
    if saved.get("input_method") in ("auto", "system", "local"):
        settings["input_method"] = saved["input_method"]
    for key in ("line_numbers", "word_wrap"):
        if type(saved.get(key)) is bool:
            settings[key] = saved[key]
    size = saved.get("font_size")
    if type(size) is int and 8 <= size <= 40:
        settings["font_size"] = size
    if isinstance(saved.get("font_family"), str):
        settings["font_family"] = saved["font_family"]
    if type(saved.get("tab_width")) is int and 1 <= saved["tab_width"] <= 16:
        settings["tab_width"] = saved["tab_width"]
    disabled = saved.get("disabled_plugins")
    if isinstance(disabled, list) and len(disabled) <= 10000 and all(
            isinstance(name, str) and 0 < len(name) <= 255 and name not in (".", "..") and
            not name.startswith("_") and not any(char in name for char in "/\\\0") for name in disabled):
        settings["disabled_plugins"] = sorted(set(disabled))
    pins=saved.get('sidebar_plugins')
    if isinstance(pins,list) and len(pins)<=100 and all(isinstance(item,str) and item.isidentifier() and not item.startswith('_') for item in pins):settings['sidebar_plugins']=list(dict.fromkeys(pins))
    active=saved.get('sidebar_active','project_explorer')
    if active is None or isinstance(active,str) and active.isidentifier():settings['sidebar_active']=active
    width=saved.get('sidebar_width',320)
    if type(width) is int and 180<=width<=900:settings['sidebar_width']=width
    return settings


class Editor:
    write_data = staticmethod(atomic_write)

    def __init__(self, root, safe_mode):
        self.root = root
        self.settings_path = SETTINGS_PATH
        self.plugins_ready = bool(safe_mode)
        self._plugins_loading = False
        self._plugin_job = None
        self._ready_callbacks = []
        self._load_token = None
        self._load_api = None
        self._quit_pending = False
        self.plugin_load_times = {}
        self.documents = []
        self.active_document = None
        self.path = None
        self.rich_dirty = False
        self.closing = False
        self.session = None
        self.change_job = None
        self.document_generation = 0
        self.document_revision = 0
        self.newline = "\n"
        self.bom = False
        self.needle = ""
        self.loaded_plugins = set()
        self.plugin_apis = {}
        self.command_palette = CommandPalette(self)
        self.quick_open = QuickOpen(self)
        self.about = AboutDialog(self, lambda: SETTINGS_PATH.with_name("pocket_editor_about.json"))
        self.safe_mode = safe_mode
        settings = read_settings()
        self.input_method = settings["input_method"]
        self.disabled_plugins = set(settings["disabled_plugins"])
        self.plugin_errors = {}
        self.plugin_manager = PluginManager(self, lambda: PLUGIN_DIR)
        self.line_numbers = tk.BooleanVar(root, value=settings["line_numbers"])
        self.word_wrap = tk.BooleanVar(root, value=settings["word_wrap"])
        self.editor_font = font.nametofont("TkFixedFont", root=root).copy()
        self.editor_font.configure(size=settings["font_size"])
        if settings["font_family"] in font.families(root):
            self.editor_font.configure(family=settings["font_family"])
        self.plugin_commands = []
        self.tab_width = settings["tab_width"]
        self.settings_window = None
        self.gutter_font = font.nametofont("TkFixedFont", root=root).copy()
        self.gutter_job = None
        self.modifier = "Command" if root.tk.call("tk", "windowingsystem") == "aqua" else "Control"

        self.setup_appearance()
        root.geometry("1280x840")
        root.minsize(480, 320)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.build_header()
        self.workspace = ttk.Frame(root)
        self.workspace.pack(fill="both", expand=True)
        self.plugin_sidebar = PluginSidebar(self, self.workspace, settings)
        editor_host = self.plugin_sidebar.editor_host
        self.document_bar = ttk.Frame(editor_host, style="Chrome.TFrame", padding=(0, 0))
        self.document_bar.pack(fill="x")
        self.tabs = ttk.Frame(self.document_bar)
        self.tabs.pack(side="left", fill="x", expand=True)
        ttk.Button(self.document_bar, text="+", width=3, command=self.new, style="Plugin.TButton").pack(side="right", padx=4)
        self.tabs.bind("<Configure>", lambda event: self.layout_tabs())
        self.document_label = ttk.Label(self.document_bar)
        self.document_state = ttk.Label(self.document_bar)
        panel = ttk.Frame(editor_host)
        self.editor_panel = panel
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(0, weight=1)
        self.buffer = DocumentText(panel, self.document_changed, undo=True, maxundo=200, wrap="none",
                              font=self.editor_font, padx=12, pady=10, width=1, height=1,
                              borderwidth=0, highlightthickness=0,
                              background="#10151b", foreground="#d1dae4",
                              insertbackground="#1597f5", selectbackground="#243f59",
                              selectforeground="#ffffff", insertwidth=2,
                              spacing1=2, spacing3=2)
        self.buffer = ActiveBuffer(root, self.buffer)
        self.gutter = tk.Canvas(panel, width=40, background=self.buffer.cget("background"),
                                highlightthickness=0, borderwidth=0, takefocus=False)
        self.gutter.grid(row=0, column=0, sticky="ns")
        vertical = ttk.Scrollbar(panel, orient="vertical", command=lambda *args: self.buffer.yview(*args))
        horizontal = ttk.Scrollbar(panel, orient="horizontal", command=lambda *args: self.buffer.xview(*args))
        self.vertical_scrollbar, self.horizontal_scrollbar = vertical, horizontal
        self.buffer.configure(yscrollcommand=lambda first, last: self.sync_scrollbar(vertical, first, last),
                              xscrollcommand=horizontal.set)
        self.buffer.grid(row=0, column=1, sticky="nsew")
        vertical.grid(row=0, column=2, sticky="ns")
        horizontal.grid(row=1, column=1, sticky="ew")
        ttk.Separator(root).pack(fill="x")
        self.status = ttk.Label(root, padding=(12, 4), style="Status.TLabel")
        self.status.pack(fill="x")
        self.add_document_record()
        self.split_editor = SplitEditor(self)
        self.build_menu()
        self.rebuild_extensions_menu()
        self.apply_settings()
        self.buffer.bind("<Configure>", self.queue_gutter)

        self.buffer.bind("<<Modified>>", self.refresh)
        self.buffer.bind("<KeyRelease>", self.refresh)
        self.buffer.bind("<ButtonRelease-1>", self.refresh)
        self.buffer.focus_set()
        self.refresh()
        if not safe_mode:
            available=[path for path in PLUGIN_DIR.glob('*.py') if not path.name.startswith('_') and path.stem not in self.disabled_plugins]
            if available:self._plugin_job=root.after(20,lambda:self.load_plugins(background=True))
            else:self.plugins_ready=True

    def setup_appearance(self):
        """UI typography is independent of the document and gutter fonts."""
        self.ui_font = font.nametofont("TkDefaultFont", root=self.root).copy()
        available = set(font.families(self.root))
        family = next((name for name in ("Inter", "Segoe UI", "SF Pro Text", "Noto Sans", "DejaVu Sans")
                       if name in available), self.ui_font.cget("family"))
        self.ui_font.configure(family=family, size=10)
        self.heading_font = self.ui_font.copy()
        self.heading_font.configure(size=10, weight="bold")
        self.caption_font = self.ui_font.copy()
        self.caption_font.configure(size=9, weight="bold")
        self.gutter_font.configure(size=10)
        self.gutter_ink = "#858585"
        self.root.configure(background="#11161c")
        # These option defaults also style plugin windows without touching Tk named fonts.
        for pattern, value in (("*Toplevel.background", "#11161c"),
                               ("*Menu.background", "#11161c"),
                               ("*Menu.foreground", "#d1dae4"),
                               ("*Menu.activeBackground", "#303b47"),
                               ("*Menu.activeForeground", "#ffffff"),
                               ("*Menu.selectColor", "#1597f5"),
                               ("*Menu.relief", "flat"),
                               ("*Menu.borderWidth", 0),
                               ("*Menu.font", self.ui_font)):
            self.root.option_add(pattern, value)
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background="#11161c", foreground="#d1dae4", font=self.ui_font,
                        bordercolor="#27323d", lightcolor="#27323d", darkcolor="#27323d")
        style.configure("TNotebook", background="#11161c", borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background="#19212a", foreground="#bdbdbd", padding=(12, 7), font=self.ui_font)
        style.map("TNotebook.Tab", background=[("disabled", "#11161c"), ("selected", "#10151b"), ("active", "#303b47")],
                  foreground=[("disabled", "#777777"), ("selected", "#ffffff"), ("active", "#ffffff")])
        style.configure("TFrame", background="#11161c")
        style.configure("Chrome.TFrame", background="#11161c")
        style.configure("TLabel", background="#11161c", foreground="#d1dae4")
        style.configure("Title.TLabel", font=self.heading_font, foreground="#eeeeee")
        style.configure("Brand.TLabel", font=(self.ui_font.cget("family"), 14), foreground="#e4eaf1")
        style.configure("Caption.TLabel", font=self.caption_font, foreground="#8e9ba9")
        style.configure("Muted.TLabel", foreground="#8e9ba9")
        style.configure("Document.TLabel", background="#10151b", foreground="#ffffff", padding=(14, 9))
        style.configure("Status.TLabel", background="#0b1016", foreground="#bdbdbd", font=self.ui_font)
        style.configure("TButton", width=0, padding=(10, 4), background="#19212a", foreground="#dddddd",
                        borderwidth=1, relief="flat", focusthickness=1, focuscolor="#1597f5")
        style.map("TButton", background=[("pressed", "#454545"), ("active", "#303b47")],
                  foreground=[("disabled", "#777777")])
        style.configure("Accent.TButton", background="#1597f5", foreground="#ffffff")
        style.map("Accent.TButton", background=[("pressed", "#005a9e"), ("active", "#168bd2")],
                  foreground=[("active", "#ffffff")])
        style.configure("Extensions.TMenubutton", padding=(9, 4), background="#19212a",
                        foreground="#d1dae4", borderwidth=0, arrowcolor="#1597f5")
        style.map("Extensions.TMenubutton", background=[("active", "#303b47")])
        style.configure("Plugin.TButton", background="#11161c", foreground="#bdbdbd", padding=(9, 4))
        style.map("Plugin.TButton", background=[("pressed", "#303b47"), ("active", "#353535")])
        style.configure("TEntry", fieldbackground="#10151b", foreground="#dddddd", padding=7,
                        insertcolor="#1597f5")
        style.configure("TCombobox", fieldbackground="#10151b", background="#19212a", padding=6,
                        foreground="#dddddd", arrowcolor="#1597f5")
        style.map("TCombobox", fieldbackground=[("readonly", "#10151b")],
                  foreground=[("readonly", "#dddddd")], selectbackground=[("readonly", "#243f59")])
        self.root.option_add("*TCombobox*Listbox.background", "#11161c")
        self.root.option_add("*TCombobox*Listbox.foreground", "#dddddd")
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#243f59")
        self.root.option_add("*TCombobox*Listbox.font", self.ui_font)
        style.configure("TSpinbox", fieldbackground="#10151b", foreground="#dddddd", padding=6,
                        background="#19212a", arrowcolor="#1597f5")
        for control in ("TCheckbutton", "TRadiobutton"):
            style.configure(control, padding=(0, 5), background="#11161c", foreground="#d1dae4")
            style.map(control, background=[("active", "#11161c")],
                      indicatorbackground=[("selected", "#1597f5"), ("!selected", "#303b47")])
        style.configure("TLabelframe", background="#11161c", bordercolor="#303b47", relief="solid")
        style.configure("TLabelframe.Label", foreground="#bbbbbb", background="#11161c")
        style.configure("TSeparator", background="#27323d")
        style.configure("Treeview", background="#11161c", fieldbackground="#11161c",
                        foreground="#d1dae4", rowheight=24, borderwidth=0)
        style.map("Treeview", background=[("selected", "#243f59")], foreground=[("selected", "#ffffff")])
        for orientation in ("Vertical", "Horizontal"):
            style.configure(orientation + ".TScrollbar", background="#465361", troughcolor="#10151b",
                            borderwidth=0, width=8, arrowsize=7, arrowcolor="#465361")
            style.map(orientation + ".TScrollbar", background=[("active", "#565656")])

    def build_header(self):
        header = ttk.Frame(self.root, padding=(14, 10), style="Chrome.TFrame")
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        brand = ttk.Frame(header)
        brand.grid(row=0, column=0, sticky="w")
        ttk.Label(brand, text="▏", foreground="#1597f5", font=self.heading_font).pack(side="left", padx=(0, 4))
        ttk.Label(brand, text="Pocket Editor", style="Brand.TLabel").pack(side="left")
        actions = ttk.Frame(header)
        actions.grid(row=0, column=1, sticky="e", padx=(16, 0))
        for label, command, style in (("New", self.new, "TButton"),
                                      ("Open…", self.open_dialog, "TButton"),
                                      ("Save", self.save, "Accent.TButton")):
            ttk.Button(actions, text=label, command=command, style=style).pack(side="left", padx=(6, 0))
        ttk.Separator(self.root).pack(fill="x")

    def document_changed(self):
        self.document_revision += 1
        if self.change_job is None and not self.closing:
            self.change_job = self.root.after_idle(self.notify_document_changed)

    def notify_document_changed(self):
        self.change_job = None
        if any(self.buffer.tag_ranges(name) for name in self.buffer.tag_names() if name.startswith("rich_")) and self.buffer.edit_modified():
            self.rich_dirty = True
        self.refresh()
        self.dispatch_plugin_event("document_changed", "editor", {})

    def cleanup_plugin(self, api):
        for callback in reversed(api._cleanups):
            try:
                callback()
            except (Exception, SystemExit) as error:
                self.root.report_callback_exception(type(error), error, error.__traceback__)
        api._cleanups.clear()
        api._subscriptions.clear()
        api._services.clear()

    def show_documentation(self):
        import webbrowser
        path = Path(__file__).resolve().parent / "docs" / "index.html"
        if not path.is_file():
            messagebox.showerror("Documentation", "HTML documentation is missing. Run tools/documentation/build_docs.py from the project folder.", parent=self.root)
            return
        try:
            opened = webbrowser.open(path.as_uri())
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            messagebox.showinfo("Documentation", f"Open this local file in your browser:\n{path}", parent=self.root)

    def build_menu(self):
        bar = self.menu_bar = tk.Menu(self.root)
        self.root.config(menu=bar)
        files = tk.Menu(bar, tearoff=False)
        edit = tk.Menu(bar, tearoff=False)
        settings = tk.Menu(bar, tearoff=False)
        view = tk.Menu(bar, tearoff=False)
        self.plugins = tk.Menu(bar, tearoff=False)
        help_menu = tk.Menu(bar, tearoff=False)
        help_menu.add_command(label="Documentation…", command=self.show_documentation)
        help_menu.add_command(label="About Pocket Editor…", command=self.about.show)
        for label, menu in (("File", files), ("Edit", edit), ("View", view), ("Settings", settings), ("Plugins", self.plugins), ("Help", help_menu)):
            if label == "Plugins":
                bar.add_command(label=label, command=self.plugin_sidebar.show_commands)
            else:
                bar.add_cascade(label=label, menu=menu)
        for label, action, key in (
            ("New", self.new, "n"), ("Open…", self.open_dialog, "o"),
            ("Quick Open…", self.quick_open.show, "p"),
            ("Save", self.save, "s"), ("Save As…", self.save_as, "Shift-s"),
            ("Close File", self.close_document, "w"),
            ("Save All", self.save_all, "Shift-a"),
            ("Close Editor", self.quit, "q"),
        ):
            self.shortcut(files, label, action, key)
        self.root.bind(f"<{self.modifier}-p>", lambda event: self.quick_open.show() or "break")
        for label, event in (
            ("Undo", "Undo"), ("Redo", "Redo"), ("Cut", "Cut"),
            ("Copy", "Copy"), ("Paste", "Paste"), ("Select All", "SelectAll"),
        ):
            edit.add_command(label=label, command=lambda event=event:self.edit_event(event))
        edit.add_separator()
        self.shortcut(edit, "Command Palette…", self.command_palette.show, "Shift-p")
        self.root.bind(f"<{self.modifier}-Shift-P>", lambda event: self.command_palette.show() or "break")
        self.shortcut(edit, "Find…", self.find, "f")
        self.shortcut(edit, "Find Next", self.find_next, "g")
        for label, callback in (("Split Editor Right", self.split_editor.enable),
                                ("Focus Other Pane", self.split_editor.other),
                                ("Close Split", self.split_editor.close)):
            view.add_command(label=label, command=callback)
        settings.add_checkbutton(label="Show Line Numbers", variable=self.line_numbers,
                                 command=self.save_settings)
        settings.add_checkbutton(label="Word Wrap", variable=self.word_wrap,
                                 command=self.save_settings)
        settings.add_separator()
        settings.add_command(label="Preferences…", command=self.preferences)
        settings.add_command(label="Vertical plugin tabs…", command=self.plugin_sidebar.choose)
        settings.add_command(label="Plugin Manager…", command=self.plugin_manager.show)
        if self.safe_mode:
            self.plugins.add_command(label="Plugins disabled in safe mode", state="disabled")
            self.plugins.add_command(label="Plugin Manager…", command=self.plugin_manager.show)
            return
        self.plugins.add_command(label="Plugin Manager…", command=self.plugin_manager.show)
        self.plugins.add_command(label="Plugin Folder…", command=self.show_plugin_folder)
        self.plugins.add_command(label="Install Example Plugin", command=self.install_example)
        self.plugins.add_command(label="Load New Plugins", command=self.load_plugins)
        self.plugins.add_separator()

    def edit_event(self, event):
        """Edit the focused text field; fall back to the active document."""
        if self.closing:return
        try:
            target=self.root.focus_get()
            if target is None or target.winfo_class() not in ("Text","Entry","TEntry","Spinbox","TSpinbox","TCombobox"):
                target=self.buffer.widget
        except (tk.TclError,KeyError):
            # Native/cloned menus need not have a Python widget wrapper.
            target=self.buffer.widget
        target.event_generate(f"<<{event}>>")

    def shortcut(self, menu, label, action, key):
        display_key = key.upper().replace("SHIFT-", "Shift+")
        modifier = "Cmd" if self.modifier == "Command" else "Ctrl"
        menu.add_command(label=label, command=action, accelerator=f"{modifier}+{display_key}")

        def invoke(_event):
            action()
            return "break"

        binding = key[:-1] + key[-1].upper() if key.startswith("Shift-") else key
        self.buffer.bind(f"<{self.modifier}-{binding}>", invoke)

    def refresh(self, _event=None):
        title = self.path.name if self.path else "Untitled"
        modified = "*" if self.buffer.edit_modified() or self.rich_dirty else ""
        self.root.title(f"{modified}{title} — Pocket Editor")
        line, column = self.buffer.index("insert").split(".")
        ending = {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}[self.newline]
        encoding = "UTF-8 BOM" if self.bom else "UTF-8"
        language = self.path.suffix.lstrip(".").upper() if self.path and self.path.suffix else "Plain text"
        self.document_label.configure(text=("●  " if modified else "") + title)
        self.document_state.configure(text="Unsaved changes" if modified else "Saved" if self.path else "New document")
        self.status.config(text=f"Ln {line}, Col {int(column) + 1}    ·    {language}    ·    {encoding}    ·    {ending}    ·    Tab width {self.tab_width}")
        self.refresh_tabs()
        self.queue_gutter()

    def may_discard(self):
        if self.rich_dirty and not messagebox.askyesno(
                "Unsaved formatting",
                "Formatting has not been saved to a formatted copy.\n"
                "Use Rich Text → Save Formatted Copy to preserve it.\n\n"
                "Discard the unsaved formatting?", parent=self.root):
            return False
        if not self.buffer.edit_modified():
            return True
        answer = messagebox.askyesnocancel("Unsaved changes", f"Save changes to {self.path.name if self.path else 'Untitled'}?",
                                            parent=self.root)
        return answer is False or (answer is True and self.save())

    def set_document(self, path, content, newline="\n", bom=False):
        self.rich_dirty = False
        self.document_generation += 1
        for tag in self.buffer.tag_names():
            if tag != "sel":
                self.buffer.tag_remove(tag, "1.0", "end")
        self.path, self.newline, self.bom = path, newline, bom
        self.buffer.replace("1.0", "end-1c", content)
        self.buffer.mark_set("insert", "1.0")
        self.buffer.see("insert")
        self.buffer.edit_reset()
        self.buffer.edit_modified(False)
        self.refresh()

    def add_document_record(self):
        document = {"widget": self.buffer.widget, "path": self.path,
                    "newline": self.newline, "bom": self.bom, "rich_dirty": self.rich_dirty}
        self.documents.append(document)
        self.active_document = document
        tab = tk.Frame(self.tabs, background="#19212a", highlightthickness=0)
        accent = tk.Frame(tab, height=2, background="#27323d")
        accent.pack(fill="x")
        label = tk.Label(tab, background="#19212a", foreground="#d1dae4",
                         font=self.ui_font, padx=10, pady=7, anchor="w")
        label.pack(side="left", fill="x", expand=True)
        close = tk.Button(tab, text="×", command=lambda: self.close_document(document),
                          background="#19212a", foreground="#d1dae4", activebackground="#454545",
                          activeforeground="white", relief="flat", borderwidth=0,
                          font=self.ui_font, padx=6, takefocus=True, highlightthickness=0)
        close.pack(side="right", padx=(0, 4), before=label)
        label.bind("<Button-1>", lambda event: self.select_document(document))
        document.update(tab=tab, label=label, close=close, accent=accent)
        document["widget"].bind("<FocusIn>", lambda event: self.select_document(document))

    def remember_document(self):
        self.active_document.update(path=self.path, newline=self.newline,
                                    bom=self.bom, rich_dirty=self.rich_dirty)

    def layout_tabs(self):
        # Wrap rows so every open file and its close button remain reachable.
        available = max(1, self.tabs.winfo_width())
        target = max(130, min(220, max((doc["label"].winfo_reqwidth() + doc["close"].winfo_reqwidth() + 14 for doc in self.documents), default=130)))
        columns = max(1, available // target)
        width = min(target, max(1, available // columns - 1))
        for index, document in enumerate(self.documents):
            tab = document["tab"]
            tab.configure(width=width, height=document["label"].winfo_reqheight() + 3)
            tab.grid_propagate(False)
            tab.pack_propagate(False)
            tab.grid(row=index // columns, column=index % columns, sticky="ew", padx=(0, 1), pady=(0, 1))

    def refresh_tabs(self):
        if not self.active_document:
            return
        self.remember_document()
        for document in self.documents:
            active = document is self.active_document
            dirty = document["widget"].edit_modified() or document["rich_dirty"]
            path = document["path"]
            title = path.name if path else "Untitled"
            if path and any(other is not document and other["path"] and other["path"].name == path.name for other in self.documents):
                title += " · " + path.parent.name
            title = title if len(title) <= 30 else title[:27] + "…"
            background = "#10151b" if active else "#19212a"
            foreground = "#e5c07b" if dirty else "#ffffff" if active else "#bdbdbd"
            document["tab"].configure(background=background)
            document["label"].configure(text=("●  " if dirty else "") + title,
                                         background=background, foreground=foreground)
            document["close"].configure(background=background, foreground="#dddddd" if active else "#8e9ba9")
            document["accent"].configure(background="#1597f5" if active else "#27323d")
        self.layout_tabs()
        if hasattr(self, "split_editor"):
            for side, document in enumerate(self.split_editor.documents):
                if document:
                    path = document["path"]
                    dirty = document["widget"].edit_modified() or document["rich_dirty"]
                    self.split_editor.panes[side][1].configure(text=("●  " if dirty else "") + (path.name if path else "Untitled"))

    def select_document(self, document):
        if document is self.active_document:
            return
        self.remember_document()
        self.buffer.attach(document["widget"])
        self.active_document = document
        self.path, self.newline, self.bom = (document[key] for key in ("path", "newline", "bom"))
        self.rich_dirty = document["rich_dirty"]
        self.document_generation += 1
        self.split_editor.show_document(document)
        self.apply_settings()
        self.vertical_scrollbar.set(*self.buffer.yview())
        self.horizontal_scrollbar.set(*self.buffer.xview())
        self.gutter.configure(background=self.buffer.cget("background"))
        self.buffer.focus_set()
        self.refresh()
        self.document_changed()
        self.dispatch_plugin_event("document_switched", "editor", {})

    def new(self):
        self.remember_document()
        previous = self.active_document
        options = {key: self.buffer.cget(key) for key in (
            "font", "padx", "pady", "background", "foreground", "insertbackground",
            "selectbackground", "selectforeground", "exportselection", "insertwidth",
            "spacing1", "spacing3")}
        widget = DocumentText(self.editor_panel, self.document_changed, undo=True, maxundo=200,
                              width=1, height=1, borderwidth=0, highlightthickness=0, **options)
        widget.configure(yscrollcommand=lambda first, last: self.sync_scrollbar(self.vertical_scrollbar, first, last),
                         xscrollcommand=self.horizontal_scrollbar.set)
        for tag in self.buffer.tag_names():
            if tag != "sel":
                options = {key: value[-1] for key, value in self.buffer.tag_configure(tag).items() if value[-1] != ""}
                widget.tag_configure(tag, **options)
        self.buffer.attach(widget)
        self.path, self.newline, self.bom, self.rich_dirty = None, "\n", False, False
        self.add_document_record()
        document = self.active_document
        self.active_document = previous
        self.buffer.attach(previous["widget"])
        self.path, self.newline, self.bom, self.rich_dirty = (previous[key] for key in ("path", "newline", "bom", "rich_dirty"))
        self.select_document(document)

    def close_document(self, document=None):
        document = document or self.active_document
        previous = self.active_document
        self.select_document(document)
        if not self.may_discard():
            self.select_document(previous)
            return False
        index = self.documents.index(document)
        if len(self.documents) == 1:
            self.new()
        target = previous if previous is not document else self.documents[max(0, index - 1)]
        if target is document:
            target = self.documents[index + 1]
        self.select_document(target)
        self.documents.remove(document)
        self.split_editor.document_closed(document)
        document["tab"].destroy()
        document["widget"].destroy()
        self.refresh_tabs()
        return True

    def save_all(self):
        previous = self.active_document
        for document in tuple(self.documents):
            if document["widget"].edit_modified() or document["path"] is None:
                self.select_document(document)
                if not self.save():
                    return False
        self.select_document(previous)
        return True

    def open_dialog(self):
        names = filedialog.askopenfilenames(parent=self.root)
        for name in names:
            self.open_path(Path(name))

    def open_path(self, path):
        path = Path(path).absolute()
        self.remember_document()
        for document in self.documents:
            if document["path"] and document["path"].resolve() == path.resolve():
                self.select_document(document)
                return
        try:
            data = path.read_bytes()
            content = data.decode("utf-8-sig")
        except (OSError, UnicodeError) as error:
            messagebox.showerror("Open failed", str(error), parent=self.root)
            return
        newline = "\r\n" if "\r\n" in content else "\r" if "\r" in content else "\n"
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if self.path is not None or self.buffer.get("1.0", "end-1c") or self.buffer.edit_modified() or self.rich_dirty:
            self.new()
        self.set_document(path, content, newline, data.startswith(codecs.BOM_UTF8))

    def save_as(self):
        name = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".txt",
            initialfile=self.path.name if self.path else "untitled.txt")
        return bool(name) and self.write(Path(name))

    def save(self):
        return self.write(self.path) if self.path else self.save_as()

    def write(self, path):
        path = Path(path).absolute()
        self.remember_document()
        if any(document is not self.active_document and document["path"] and
               document["path"].resolve() == path.resolve() for document in self.documents):
            messagebox.showerror("Save failed", "This file is already open in another tab.", parent=self.root)
            return False
        content = self.buffer.get("1.0", "end-1c").replace("\n", self.newline)
        try:
            data = content.encode("utf-8-sig" if self.bom else "utf-8")
            atomic_write(path, data)
        except (OSError, UnicodeError) as error:
            messagebox.showerror("Save failed", str(error), parent=self.root)
            return False
        self.path = path
        self.document_changed()
        self.buffer.edit_modified(False)
        self.refresh()
        self.root.after_idle(lambda: self.dispatch_plugin_event("document_saved", "editor", {"path": str(path)}))
        return True

    def quit(self):
        if self.closing:return
        if not self.plugins_ready:
            if not self._quit_pending:
                self._quit_pending=True
                self.when_plugins_ready(self.quit)
            return
        previous=self.active_document
        if self.session:
            self.select_document(previous)
            if not self.save_settings():return
            if not self.session.finish():
                messagebox.showerror('Workspace not saved','The workspace backup could not be saved. The editor remains open to protect your drafts.',parent=self.root)
                return
        else:
            for document in tuple(self.documents):
                self.select_document(document)
                if not self.may_discard():
                    self.select_document(previous);return
            self.select_document(previous)
            if not self.save_settings():return
        self.closing = True
        self.quick_open.close()
        self.plugin_sidebar.cleanup()
        for api in reversed(tuple(self.plugin_apis.values())):
            self.cleanup_plugin(api)
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():
                yield from descendants(child)
        owners = list(descendants(self.root))
        for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            script = self.root.tk.call("after", "info", job)[0]
            command = self.root.tk.splitlist(script)[0]
            owner = next((widget for widget in owners
                          if command in (widget._tclCommands or ())), self.root)
            owner.after_cancel(job)
        self.root.destroy()

    def apply_settings(self):
        for document in self.documents:
            document["widget"].configure(tabs=(self.editor_font.measure(" " * self.tab_width),),
                                         wrap="word" if self.word_wrap.get() else "none")
        if self.line_numbers.get():
            self.gutter.grid()
        else:
            self.gutter.grid_remove()
        if hasattr(self, "split_editor"):
            self.split_editor.layout()
        self.queue_gutter()

    def save_settings(self):
        self.apply_settings()
        settings = {
            **self.plugin_sidebar.settings(),
            "line_numbers": self.line_numbers.get(),
            "word_wrap": self.word_wrap.get(),
            "font_size": self.editor_font.cget("size"),
            "font_family": self.editor_font.cget("family"),
            "tab_width": self.tab_width,
            "input_method": self.input_method,
            "disabled_plugins": sorted(self.disabled_plugins),
        }
        try:
            atomic_write(SETTINGS_PATH, (json.dumps(settings, indent=2) + "\n").encode("utf-8"))
        except OSError as error:
            messagebox.showerror("Settings could not be saved", str(error), parent=self.root)
            return False
        return True

    def preferences(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title("Preferences")
        window.transient(self.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=20)
        body.pack(fill="both", expand=True)
        family = tk.StringVar(window, self.editor_font.cget("family"))
        size = tk.StringVar(window, str(self.editor_font.cget("size")))
        tabs = tk.StringVar(window, str(self.tab_width))
        numbers = tk.BooleanVar(window, self.line_numbers.get())
        wrap = tk.BooleanVar(window, self.word_wrap.get())
        input_method = tk.StringVar(window, self.input_method)
        typography = ttk.LabelFrame(body, text="Document font", padding=12)
        typography.pack(fill="x")
        ttk.Label(typography, text="Font family").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(typography, textvariable=family, width=32, state="readonly",
                     values=sorted(set(font.families(window)))).grid(row=0, column=1, padx=12)
        ttk.Label(typography, text="Size (points)").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Spinbox(typography, from_=8, to=40, textvariable=size, width=8).grid(
            row=1, column=1, sticky="w", padx=12)
        layout = ttk.LabelFrame(body, text="Document layout", padding=12)
        layout.pack(fill="x", pady=12)
        ttk.Checkbutton(layout, text="Show line numbers", variable=numbers).pack(anchor="w")
        ttk.Checkbutton(layout, text="Wrap long lines", variable=wrap).pack(anchor="w", pady=6)
        row = ttk.Frame(layout)
        row.pack(fill="x")
        ttk.Label(row, text="Tab width (spaces)").pack(side="left")
        ttk.Spinbox(row, from_=1, to=16, textvariable=tabs, width=8).pack(side="right")
        keyboard = ttk.LabelFrame(body, text="Keyboard input", padding=12)
        keyboard.pack(fill="x", pady=(0,12))
        ttk.Label(keyboard,text="Input method (restart required)").pack(side="left")
        ttk.Combobox(keyboard,textvariable=input_method,values=("auto","system","local"),
                     state="readonly",width=10).pack(side="right",padx=(12,0))
        ttk.Label(body,text="Auto uses local compose for the affected Linux/Tk/IBus combination.\n"
                           "Choose system for external IME composition; restart to apply.",
                  style="Muted.TLabel").pack(anchor="w",pady=(0,12))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")

        def apply(close=False):
            try:
                point_size, tab_size = int(size.get()), int(tabs.get())
                if not 8 <= point_size <= 40 or not 1 <= tab_size <= 16:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid setting",
                                     "Font size must be 8–40 and tab width 1–16.", parent=window)
                return
            self.editor_font.configure(family=family.get(), size=point_size)
            self.tab_width = tab_size
            self.line_numbers.set(numbers.get())
            self.word_wrap.set(wrap.get())
            self.input_method = input_method.get()
            self.save_settings()
            if close:
                window.destroy()

        def defaults():
            family.set(font.nametofont("TkFixedFont", root=self.root).cget("family"))
            size.set("11")
            tabs.set("4")
            numbers.set(True)
            wrap.set(False)
            input_method.set("auto")

        ttk.Button(buttons, text="Restore Defaults", command=defaults).pack(side="left")
        ttk.Button(buttons, text="OK", command=lambda: apply(True)).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Apply", command=apply).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())

    def sync_scrollbar(self, scrollbar, first, last):
        scrollbar.set(first, last)
        self.queue_gutter()

    def queue_gutter(self, _event=None):
        if self.line_numbers.get() and self.gutter_job is None:
            self.gutter_job = self.root.after_idle(self.draw_gutter)

    def draw_gutter(self):
        self.gutter_job = None
        if self.line_numbers.get():
            self.split_editor.draw_gutters()

    def find(self):
        selection = self.buffer.tag_ranges("sel")
        initial = self.buffer.get(*selection) if selection else self.needle
        needle = simpledialog.askstring("Find", "Find text (case sensitive)",
                                         initialvalue=initial, parent=self.root)
        if needle:
            self.needle = needle
            self.find_next()

    def find_next(self):
        if not self.needle:
            return
        selection = self.buffer.tag_ranges("sel")
        start = selection[-1] if selection else "insert"
        count = tk.IntVar(master=self.root)
        match = self.buffer.search(self.needle, start, stopindex="end-1c",
                                   exact=True, count=count)
        if not match:
            match = self.buffer.search(self.needle, "1.0", stopindex=start,
                                       exact=True, count=count)
        if not match:
            self.root.bell()
            return
        self.buffer.tag_remove("sel", "1.0", "end")
        self.buffer.tag_add("sel", match, f"{match}+{count.get()}c")
        self.buffer.mark_set("insert", match)
        self.buffer.see(match)
        self.refresh()

    def show_plugin_folder(self):
        try:
            PLUGIN_DIR.mkdir(exist_ok=True)
        except OSError as error:
            messagebox.showerror("Plugin folder", str(error), parent=self.root)
            return
        simpledialog.askstring("Plugin folder", "Copy this path and open it in your file manager.",
                               initialvalue=str(PLUGIN_DIR), parent=self.root)

    def install_example(self):
        path = PLUGIN_DIR / "word_count.py"
        try:
            PLUGIN_DIR.mkdir(exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(EXAMPLE)
        except FileExistsError:
            pass
        except OSError as error:
            messagebox.showerror("Example plugin", str(error), parent=self.root)
            return
        self.load_plugins()

    def when_plugins_ready(self, callback):
        if self.plugins_ready:self.root.after_idle(callback)
        else:self._ready_callbacks.append(callback)

    def _register_plugin(self, path):
        api=PluginAPI(self,path.stem)
        started=time.perf_counter()
        try:
            plugin=runpy.run_path(str(path),run_name=path.stem)
            plugin['register'](api)
        except (Exception,SystemExit) as error:
            self.cleanup_plugin(api)
            self.plugin_errors[path.stem]=f'{type(error).__name__}: {error}'[:4000]
            return path.name+': '+self.plugin_errors[path.stem]
        finally:self.plugin_load_times[path.stem]=time.perf_counter()-started
        for label,callback in api._commands:
            command=lambda callback=callback,name=path.name:self.run_plugin(name,callback)
            self.plugins.add_command(label=label,command=command)
            key=f'{path.stem}:{label}'
            self.plugin_commands.append((key,label,command))
        self.plugin_errors.pop(path.stem,None)
        self.loaded_plugins.add(path);self.plugin_apis[path.stem]=api
        api.emit('plugin_loaded',plugin_id=path.stem)
        return None

    def load_plugins(self, background=False):
        if self.safe_mode or self.closing or self._plugins_loading:return
        if self._plugin_job is not None:
            self.root.after_cancel(self._plugin_job);self._plugin_job=None
        paths=[path for path in sorted(PLUGIN_DIR.glob('*.py')) if not path.name.startswith('_')
               and path not in self.loaded_plugins and path.stem not in self.disabled_plugins]
        # The lightweight visual service loads first; all other plugins retain filename order.
        paths.sort(key=lambda path:path.stem!='loading_indicator')
        self._plugins_loading=True
        errors=[];total=len(paths)
        def finish():
            self._plugin_job=None;self._plugins_loading=False;self.plugins_ready=True
            if self._load_api and self._load_token is not None:
                self._load_api.call_service('loading_indicator.finish',self._load_token)
                self._load_token=None
            self.plugin_apis=dict(sorted(self.plugin_apis.items()))
            self.rebuild_extensions_menu()
            self.dispatch_plugin_event('plugins_loaded','editor',{})
            self.refresh()
            callbacks,self._ready_callbacks=self._ready_callbacks,[]
            for callback in callbacks:self.root.after_idle(callback)
            if errors:messagebox.showerror('Plugin loading errors','\n'.join(errors),parent=self.root)
        def step():
            self._plugin_job=None
            if self.closing:return
            if not paths:finish();return
            path=paths.pop(0)
            caption=f'Loading {path.stem.replace("_"," ").title()}… ({total-len(paths)}/{total})'
            self.status.configure(text=caption)
            if self._load_api and self._load_token is not None:
                self._load_api.call_service('loading_indicator.update',self._load_token,message=caption,
                    progress=(total-len(paths))/max(1,total))
            error=self._register_plugin(path)
            if error:errors.append(error)
            if path.stem=='loading_indicator' and not error:
                self._load_api=self.plugin_apis[path.stem]
                self._load_token=self._load_api.call_service('loading_indicator.start',message='Preparing plugins…')
            if background:self._plugin_job=self.root.after(1,step)
        if background:step()
        else:
            while paths:step()
            finish()

    def rebuild_extensions_menu(self):
        self.plugin_sidebar.refresh()

    def dispatch_plugin_event(self, event, source, payload):
        if self.closing:
            return
        for api in tuple(self.plugin_apis.values()):
            for name, callback in tuple(api._subscriptions):
                if name == event:
                    try:
                        callback(source=source, **payload)
                    except (Exception, SystemExit) as error:
                        self.root.report_callback_exception(type(error), error, error.__traceback__)

    def run_plugin(self, name, callback, _loading_shown=False):
        if self.closing:return
        owner=Path(name).stem
        if not _loading_shown and owner in self.plugin_apis:
            if self.plugin_sidebar.defer_window(owner,callback,lambda:self.run_plugin(name,callback,_loading_shown=True)):return
        try:
            callback()
        except (Exception, SystemExit) as error:
            self.plugin_errors[Path(name).stem] = f"{type(error).__name__}: {error}"[:4000]
            messagebox.showerror(f"Plugin error — {name}", str(error), parent=self.root)


def main():
    parser = argparse.ArgumentParser(description="Pocket Editor, a portable UTF-8 editor")
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--safe-mode", action="store_true", help="start with plugins disabled")
    parser.add_argument("--no-session", action="store_true", help="start without session restore or draft backups")
    parser.add_argument('--input-method',choices=('auto','system','local'),
                        help='X11 keyboard backend; system enables external IME composition')
    args = parser.parse_args()
    root = create_root(args.input_method or read_settings()['input_method'])
    editor = Editor(root, args.safe_mode)
    def start_session():
        from editor_session import EditorSession
        api=editor.plugin_apis.get('loading_indicator');token=None
        if api and not args.no_session:
            try:token=api.call_service('loading_indicator.start',message='Restoring your workspace…')
            except LookupError:pass
            root.update_idletasks()
        try:
            if not args.no_session:
                if not EditorSession(editor,SETTINGS_PATH.with_name('pocket_editor_session.json')).start():
                    editor.status.configure(text='Another editor owns this workspace session. Close it before testing automatic restore.')
            if args.file:editor.open_path(args.file)
        finally:
            if api and token is not None:
                try:api.call_service('loading_indicator.finish',token)
                except LookupError:pass
    editor.when_plugins_ready(start_session)
    root.mainloop()


if __name__ == "__main__":
    main()
