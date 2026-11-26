"""Pocket Editor search and replace plugin with optional Python regex syntax."""
import re
import json
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path


def compile_search(query, regex=False, match_case=False):
    if not query:
        raise ValueError("Enter text to find.")
    return re.compile(query if regex else re.escape(query), 0 if match_case else re.IGNORECASE)


def replace_matches(source, pattern, replacement, regex):
    return pattern.subn(replacement if regex else lambda match: replacement, source)


REGEX_WORKER = r"""
import json, re, sys
source, query, regex, case, replacement, operation = json.load(sys.stdin)
try:
    pattern = re.compile(query if regex else re.escape(query), 0 if case else re.IGNORECASE)
    if operation == "replace_all":
        result = pattern.subn(replacement if regex else lambda m: replacement, source)
    else:
        result = []
        for match in pattern.finditer(source):
            if len(result) >= 100000:
                raise ValueError("Too many matches; narrow the search.")
            result.append([match.start(), match.end(),
                           match.expand(replacement) if operation == "replace_one" and regex else replacement])
    json.dump({"result": result}, sys.stdout)
except (ValueError, re.error, IndexError, RecursionError) as error:
    json.dump({"error": str(error)}, sys.stdout)
"""


def bounded_search(source, query, regex, case, replacement="", operation="find"):
    """Run untrusted regex in a disposable process with a bounded GUI pause."""
    if not query:
        raise ValueError("Enter text to find.")
    try:
        process = subprocess.run([sys.executable, "-c", REGEX_WORKER],
            input=json.dumps([source, query, regex, case, replacement, operation]),
            capture_output=True, encoding="utf-8", timeout=0.5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        raise ValueError("Search exceeded 0.5 seconds; simplify the expression.") from None
    except OSError as error:
        raise ValueError(str(error)) from error
    if process.returncode:
        raise ValueError("Search worker failed: " + process.stderr[-500:])
    result = json.loads(process.stdout)
    if "error" in result:
        raise ValueError(result["error"])
    return result["result"]


class SearchMatch:
    def __init__(self, values):
        self.values = values

    def start(self):
        return self.values[0]

    def end(self):
        return self.values[1]

    def expand(self, replacement):
        return self.values[2]


def register(api):
    editor, root, text = api._editor, api._editor.root, api._editor.buffer
    window = None
    dock_entry = None
    active = None
    settings_path = editor.settings_path.with_name("search_replace_settings.json")
    try:
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        opacity_value = saved.get("opacity", 100)
        if type(opacity_value) not in (int, float) or not 40 <= opacity_value <= 100:
            opacity_value = 100
    except (OSError, ValueError, TypeError, AttributeError):
        opacity_value = 100
    query, replacement, status = tk.StringVar(root), tk.StringVar(root), tk.StringVar(root)
    regex, case = tk.BooleanVar(root, False), tk.BooleanVar(root, False)
    opacity = tk.DoubleVar(root, opacity_value)
    opacity_label = tk.StringVar(root, f"Window opacity: {opacity_value:.0f}%")
    text.configure(exportselection=False)

    def save_opacity(_value=None):
        value = max(40, min(100, opacity.get()))
        opacity_label.set(f"Window opacity: {value:.0f}%")
        if window is not None and window.winfo_exists():
            try:
                window.attributes('-alpha', value / 100)
            except tk.TclError:
                status.set("Window transparency is unavailable on this desktop.")

    def persist_opacity(_event=None):
        try:
            editor.write_data(settings_path, (json.dumps({'opacity': round(opacity.get())})+'\n').encode('utf-8'))
        except OSError as error:
            status.set(f"Could not save opacity: {error}")

    def matches(operation="find"):
        source = api.get_text()
        found = bounded_search(source, query.get(), regex.get(), case.get(), replacement.get(), operation)
        return source, None, [SearchMatch(values) for values in found]

    def index(offset):
        # Text index arithmetic counts characters, unlike Tcl string length.
        return f"1.0 +{offset}c"

    def cursor_offset():
        return len(text.get("1.0", "insert"))

    def select(source, match, number, total):
        nonlocal active
        start, end = index(match.start()), index(match.end())
        text.tag_remove("sel", "1.0", "end")
        text.tag_add("sel", start, end)
        text.mark_set("insert", start)
        text.see(start)
        active = (source, query.get(), regex.get(), case.get(), match.start(), match.end())
        suffix = " (zero-length match)" if match.start() == match.end() else ""
        status.set(f"Match {number + 1} of {total}{suffix}")
        editor.refresh()

    def find(backwards=False):
        nonlocal active
        try:
            source, _, found = matches()
        except (ValueError, re.error) as error:
            status.set(str(error))
            return
        if not found:
            active = None
            status.set("No matches.")
            return
        signature = (source, query.get(), regex.get(), case.get())
        if active and active[:4] == signature and cursor_offset() == active[4]:
            current = next((i for i, match in enumerate(found)
                            if (match.start(), match.end()) == active[4:]), None)
            if current is not None:
                target = (current + (-1 if backwards else 1)) % len(found)
                select(source, found[target], target, len(found))
                return
        cursor = cursor_offset()
        candidates = [i for i, match in enumerate(found)
                      if (match.start() < cursor if backwards else match.start() >= cursor)]
        target = (candidates[-1] if backwards else candidates[0]) if candidates else (
            len(found) - 1 if backwards else 0)
        select(source, found[target], target, len(found))

    def replace_one():
        nonlocal active
        try:
            source, _, found = matches("replace_one")
            signature = (source, query.get(), regex.get(), case.get())
            if not active or active[:4] != signature or cursor_offset() != active[4]:
                find()
                return
            match = next((m for m in found if (m.start(), m.end()) == active[4:]), None)
            if match is None:
                find()
                return
            value = match.expand(replacement.get()) if regex.get() else replacement.get()
        except (ValueError, re.error, IndexError) as error:
            status.set(str(error))
            return
        start, end = index(match.start()), index(match.end())
        text.edit_separator()
        text.replace(start, end, value)
        text.edit_separator()
        text.mark_set("insert", f"{start} +{len(value)}c")
        active = None
        editor.refresh()
        if hasattr(api, "emit"):
            api.emit("document_changed")
        status.set("Replaced one match. Use Next to continue.")

    def replace_all():
        nonlocal active
        try:
            source = api.get_text()
            formatted, count = bounded_search(source, query.get(), regex.get(), case.get(),
                                               replacement.get(), "replace_all")
        except (ValueError, re.error, IndexError) as error:
            status.set(str(error))
            return
        if formatted != source:
            api.replace_text(formatted)
            editor.refresh()
        active = None
        status.set(f"Replaced {count} matches.")


    def close_window():
        nonlocal window
        if window is not None:
            persist_opacity()
            window.destroy()
            window = None

    def build(parent, separate=False):
        outer = ttk.Frame(parent)
        outer.pack(fill='both', expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0,
                           background=text.cget('background'), width=240)
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y');canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(canvas, padding=12)
        item = canvas.create_window((0, 0), window=body, anchor='nw')
        body.columnconfigure(0, weight=1)
        body.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
        entries = []
        for row, (label, variable) in enumerate((("Find", query), ("Replace with", replacement))):
            ttk.Label(body, text=label).grid(row=row*2, column=0, sticky='w', pady=(4, 3))
            entry = ttk.Entry(body, textvariable=variable)
            entry.grid(row=row*2+1, column=0, sticky='ew', pady=(0, 8))
            entry.bind('<Return>', lambda _event: find() or 'break')
            entry.bind('<Shift-Return>', lambda _event: find(True) or 'break')
            entries.append(entry)
        ttk.Checkbutton(body, text='Regular expression', variable=regex).grid(row=4, column=0, sticky='w')
        ttk.Checkbutton(body, text='Match case', variable=case).grid(row=5, column=0, sticky='w', pady=(3, 8))
        hint = ttk.Label(body, text=r'Regex: Python syntax. Capture replacements: \1 or \g<name>.', wraplength=230)
        hint.grid(row=6, column=0, sticky='ew', pady=(0, 8))
        buttons = ttk.Frame(body);buttons.grid(row=7, column=0, sticky='ew')
        buttons.columnconfigure((0, 1), weight=1)
        for i, (label, callback) in enumerate((("Previous", lambda: find(True)), ("Next", find),
                                              ("Replace", replace_one), ("Replace All", replace_all))):
            ttk.Button(buttons, text=label, command=callback).grid(row=i//2, column=i%2, sticky='ew', padx=2, pady=3)
        feedback = ttk.Label(body, textvariable=status, wraplength=230)
        feedback.grid(row=8, column=0, sticky='ew', pady=10)
        ttk.Separator(body).grid(row=9, column=0, sticky='ew', pady=6)
        ttk.Button(body, text='Use vertical tab' if separate else 'Open separate window',
                   command=use_dock if separate else show_window).grid(row=10, column=0, sticky='ew', pady=5)
        ttk.Label(body, textvariable=opacity_label).grid(row=11, column=0, sticky='w', pady=(10, 3))
        scale = ttk.Scale(body, from_=40, to=100, variable=opacity, command=save_opacity)
        scale.grid(row=12, column=0, sticky='ew')
        scale.bind('<ButtonRelease-1>', persist_opacity)
        scale.bind('<KeyRelease>', persist_opacity)
        ttk.Label(body, text='Applies only to the separate search window.', wraplength=230).grid(row=13, column=0, sticky='ew', pady=(4, 8))
        def resize(event):
            for label in body.winfo_children():
                if isinstance(label, ttk.Label):label.configure(wraplength=max(80, event.width-24))
        body.bind('<Configure>', resize, add='+')
        def scroll(event):
            canvas.yview_scroll(-1 if (getattr(event, 'num', None)==4 or getattr(event, 'delta', 0)>0) else 1, 'units')
            return 'break'
        def bind_scroll(widget):
            for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):widget.bind(sequence, scroll, add='+')
            for child in widget.winfo_children():bind_scroll(child)
        bind_scroll(outer)
        return outer, entries[0]

    def seed_selection():
        selection = text.tag_ranges('sel')
        if selection:
            value = text.get(*selection)
            if value and '\n' not in value and len(value) <= 1000: query.set(value)

    def mount(parent):
        nonlocal dock_entry
        panel, dock_entry = build(parent)
        root.after_idle(focus_dock)
        return panel

    def focus_dock():
        if dock_entry is not None and dock_entry.winfo_exists() and dock_entry.winfo_ismapped():
            dock_entry.focus_set();dock_entry.selection_range(0, 'end')

    def show(seed=True):
        if seed:seed_selection()
        editor.plugin_sidebar.open(api.plugin_id)
        root.after_idle(focus_dock)
        root.after(180, focus_dock)

    def use_dock():
        close_window();show(False)

    def show_window():
        nonlocal window
        if window is not None and window.winfo_exists():
            window.lift();return
        window = tk.Toplevel(root)
        window.title('Search and Replace');window.transient(root)
        window.geometry('420x590');window.minsize(300, 300)
        _, entry = build(window, separate=True)
        save_opacity()
        window.protocol('WM_DELETE_WINDOW', close_window)
        window.bind('<Escape>', lambda _event: close_window())
        entry.focus_set();entry.selection_range(0, 'end')

    def next_match(backwards=False):
        if query.get():find(backwards)
        else:show()

    def reset_active(**_payload):
        nonlocal active
        active = None
        text.configure(exportselection=False)

    def cleanup():
        if window is not None and window.winfo_exists():close_window()

    api.add_panel(mount, width=340)
    api.add_command('Search & Replace', show)
    api.add_command('Search & Replace — Separate Window', show_window)
    api.provide_service('show', show)
    api.provide_service('next', next_match)
    api.provide_service('previous', lambda: next_match(True))
    api.on('document_switched', reset_active)
    api.on_cleanup(cleanup)
    for key, action in (('h', show), ('Shift-G', lambda: next_match(True))):
        sequence = f'<{editor.modifier}-{key}>'
        binding = editor.buffer.bind(sequence, lambda _event, action=action: action() or 'break')
        api.on_cleanup(lambda sequence=sequence, binding=binding: editor.buffer.unbind(sequence, binding))
    binding = editor.buffer.bind('<Shift-F3>', lambda _event: next_match(True) or 'break')
    api.on_cleanup(lambda: editor.buffer.unbind('<Shift-F3>', binding))
