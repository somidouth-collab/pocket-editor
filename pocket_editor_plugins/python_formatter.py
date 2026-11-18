"""Pocket Editor Python formatter. Requires Black in the editor's Python environment."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk


def format_source(source, filename, line_length):
    command = [sys.executable, "-m", "black", "--quiet", "--line-length", str(line_length),
               "--stdin-filename", str(filename), "-"]
    try:
        result = subprocess.run(command, input=source, capture_output=True,
                                encoding="utf-8", timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Formatting exceeded 60 seconds. The document was left intact.") from None
    except OSError as error:
        raise RuntimeError(str(error)) from error
    if result.returncode:
        error = result.stderr.strip()
        if "No module named black" in error:
            raise RuntimeError(
                "Install Black in the Python environment that runs Pocket Editor.\n\n"
                f'Python executable: {sys.executable}\n'
                'Command: python -m pip install black\n\n'
                'On Debian system Python, you can use: sudo apt install black'
            )
        raise RuntimeError(error or "Black could not format this document.")
    return result.stdout


def register(api):
    editor = api._editor
    root = editor.root
    settings_file = Path(__file__).with_name("python_formatter_settings.json")
    length = 88
    try:
        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        candidate = saved.get("line_length") if isinstance(saved, dict) else None
        if type(candidate) is int and 40 <= candidate <= 200:
            length = candidate
    except (OSError, ValueError):
        pass
    busy = False
    results = queue.Queue()
    window = None

    def format_document():
        nonlocal busy
        if busy:
            return
        path = api.get_path()
        if path is None or path.suffix.lower() not in (".py", ".pyw", ".pyi"):
            messagebox.showinfo("Python files only",
                                "Open a Python file, or save this document with a .py extension first.",
                                parent=root)
            return
        generation = editor.document_generation
        source = api.get_text()
        busy = True

        def worker():
            try:
                results.put((format_source(source, path, length), None))
            except Exception as error:
                results.put((None, str(error)))

        def poll():
            nonlocal busy
            try:
                formatted, error = results.get_nowait()
            except queue.Empty:
                root.after(100, poll)
                return
            busy = False
            if error:
                messagebox.showerror("Python formatting failed", error, parent=root)
                return
            if editor.document_generation != generation or api.get_path() != path or api.get_text() != source:
                messagebox.showinfo("Document changed",
                                    "The document changed during formatting. Click Format Python again.",
                                    parent=root)
                return
            if formatted == source:
                messagebox.showinfo("Python formatting", "This document is already formatted.", parent=root)
                return
            insertion = editor.buffer.index("insert")
            view = editor.buffer.yview()[0]
            api.replace_text(formatted)
            editor.buffer.mark_set("insert", insertion)
            editor.buffer.yview_moveto(view)
            editor.refresh()
            if hasattr(api, "emit"):
                api.emit("document_formatted", language="Python")

        threading.Thread(target=worker, daemon=True).start()
        root.after(100, poll)

    def preferences():
        nonlocal window, length
        if window is not None and window.winfo_exists():
            window.lift()
            return
        window = tk.Toplevel(root)
        window.title("Python Formatter")
        window.transient(root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        value = tk.StringVar(window, str(length))
        ttk.Label(body, text="Line length").grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Spinbox(body, from_=40, to=200, textvariable=value, width=8).grid(row=0, column=1)
        ttk.Label(body, text="Black formats the whole open Python document.\n"
                            "Use Undo to revert, then Save when ready.\n"
                            "Black also respects project configuration and # fmt: off regions.").grid(
                                row=1, column=0, columnspan=2, pady=14, sticky="w")

        def save():
            nonlocal length
            try:
                selected = int(value.get())
                if not 40 <= selected <= 200:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Line length", "Enter a number from 40 to 200.", parent=window)
                return
            try:
                editor.write_data(settings_file, json.dumps({"line_length": selected}, indent=2).encode("utf-8"))
            except OSError as error:
                messagebox.showerror("Settings", str(error), parent=window)
                return
            length = selected
            window.destroy()

        ttk.Button(body, text="Save Settings", command=save).grid(row=2, column=1, sticky="e")

    if hasattr(api, "provide_service"):
        api.provide_service("format", format_document)
    api.add_command("Format Python", format_document)
    api.add_command("Python Format Settings", preferences)
