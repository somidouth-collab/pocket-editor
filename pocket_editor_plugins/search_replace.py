"""Pocket Editor search and replace plugin with optional Python regex syntax."""
import re
import json
import subprocess
import sys
import tkinter as tk
from tkinter import ttk


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
            capture_output=True, encoding="utf-8", timeout=0.5)
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
    editor = api._editor
    root = editor.root
    text = editor.buffer
    window = None
    text.configure(exportselection=False)

    def show():
        nonlocal window
        if window is not None and window.winfo_exists():
            window.lift()
            return
        window = tk.Toplevel(root)
        window.title("Search and Replace")
        window.transient(root)
        window.minsize(520, 240)
        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        selection = text.tag_ranges("sel")
        initial = text.get(*selection) if selection else ""
        query = tk.StringVar(window, initial)
        replacement = tk.StringVar(window)
        regex = tk.BooleanVar(window, False)
        case = tk.BooleanVar(window, False)
        status = tk.StringVar(window)
        active = None
        for row, (label, variable) in enumerate((("Find", query), ("Replace with", replacement))):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            entry = ttk.Entry(body, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            if row == 0:
                find_entry = entry
        options = ttk.Frame(body)
        options.grid(row=2, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Checkbutton(options, text="Regular expression", variable=regex).pack(side="left")
        ttk.Checkbutton(options, text="Match case", variable=case).pack(side="left", padx=12)
        ttk.Label(body, text=r"Regex uses Python syntax. Capture replacements: \1 or \g<name>.").grid(
            row=3, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Label(body, textvariable=status, wraplength=500).grid(
            row=5, column=0, columnspan=2, sticky="w")

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

        for label, callback in (("Previous", lambda: find(True)), ("Next", find),
                                ("Replace", replace_one), ("Replace All", replace_all)):
            ttk.Button(buttons, text=label, command=callback).pack(side="left", padx=(0, 6))
        window.bind("<Return>", lambda event: find())
        window.bind("<Escape>", lambda event: window.destroy())
        find_entry.focus_set()
        find_entry.selection_range(0, "end")

    api.add_command("Search & Replace", show)
