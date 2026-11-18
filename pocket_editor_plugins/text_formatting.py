"""Pocket Editor plugin. Place in pocket_editor_plugins and load new plugins."""
import re
import tkinter as tk
from tkinter import messagebox, ttk


def transform(text, operation, width=4):
    """Transform text while retaining its final newline."""
    if operation == "UPPERCASE":
        return text.upper()
    if operation == "lowercase":
        return text.lower()
    if operation == "Title Case":
        return text.title()
    if operation == "Trim trailing spaces":
        return re.sub(r"[ \t]+(?=\n|$)", "", text)
    if operation == "Tabs to spaces":
        return text.expandtabs(width)
    if operation == "Indent":
        return re.sub(r"(?m)^(?=.)", " " * width, text)
    if operation == "Unindent":
        return re.sub(r"(?m)^(?:\t| {1," + str(width) + r"})", "", text)
    if operation == "Collapse blank lines":
        return re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", text)
    if operation in ("Sort lines A–Z", "Sort lines Z–A", "Remove duplicate lines"):
        final_newline = text.endswith("\n")
        lines = text.split("\n")
        if final_newline:
            lines.pop()
        if operation == "Remove duplicate lines":
            lines = list(dict.fromkeys(lines))
        else:
            lines.sort(key=str.casefold, reverse=operation == "Sort lines Z–A")
        return "\n".join(lines) + ("\n" if final_newline else "")
    raise ValueError("Unknown formatting operation")


OPERATIONS = (
    "UPPERCASE", "lowercase", "Title Case", "Trim trailing spaces",
    "Tabs to spaces", "Indent", "Unindent", "Collapse blank lines",
    "Sort lines A–Z", "Sort lines Z–A", "Remove duplicate lines",
)


def register(api):
    editor = api._editor
    current_window = None

    def open_formatter():
        nonlocal current_window
        if current_window is not None and current_window.winfo_exists():
            current_window.lift()
            return
        text_widget = editor.buffer
        selection = text_widget.tag_ranges("sel")
        start, end = selection if selection else ("1.0", "end-1c")
        generation = editor.document_generation
        original = text_widget.get(start, end)
        text_widget.mark_set("format_start", start)
        text_widget.mark_gravity("format_start", "left")
        text_widget.mark_set("format_end", end)
        text_widget.mark_gravity("format_end", "right")
        window = tk.Toplevel(editor.root)
        current_window = window
        window.title("Text Formatting")
        window.transient(editor.root)
        window.geometry("660x460")
        window.minsize(480, 340)
        window.grab_set()
        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Selected text" if selection else "Entire document").pack(anchor="w")
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=12)
        operation = tk.StringVar(window, OPERATIONS[0])
        choice = ttk.Combobox(controls, textvariable=operation, values=OPERATIONS,
                              state="readonly", width=28)
        choice.pack(side="left")
        ttk.Label(controls, text="Spaces per tab / indent").pack(side="left", padx=10)
        width = tk.StringVar(window, str(getattr(editor, "tab_width", 4)))
        spin = ttk.Spinbox(controls, from_=1, to=16, textvariable=width, width=4)
        spin.pack(side="left")
        ttk.Label(body, text="Preview").pack(anchor="w", pady=(0, 6))
        panel = ttk.Frame(body)
        panel.pack(fill="both", expand=True)
        preview = tk.Text(panel, wrap="none", font=editor.editor_font, height=12)
        vertical = ttk.Scrollbar(panel, command=preview.yview)
        horizontal = ttk.Scrollbar(panel, orient="horizontal", command=preview.xview)
        preview.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        preview.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        status = ttk.Label(body)
        status.pack(anchor="w", pady=8)

        def result():
            spaces = int(width.get())
            if not 1 <= spaces <= 16:
                raise ValueError
            return transform(original, operation.get(), spaces)

        def refresh(*_):
            try:
                formatted = result()
            except (ValueError, tk.TclError):
                status.config(text="Enter a tab / indent width from 1 to 16.")
                apply_button.config(state="disabled")
                return
            preview.config(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", formatted)
            preview.config(state="disabled")
            status.config(text=f"{len(original):,} → {len(formatted):,} characters")
            apply_button.config(state="normal")

        def close():
            text_widget.mark_unset("format_start", "format_end")
            window.destroy()
            text_widget.focus_set()

        def apply():
            try:
                formatted = result()
            except ValueError:
                return
            if editor.document_generation != generation or text_widget.get("format_start", "format_end") != original:
                messagebox.showerror("Document changed",
                                     "Close this window and select the text again.", parent=window)
                return
            if formatted != original:
                text_widget.edit_separator()
                text_widget.replace("format_start", "format_end", formatted)
                text_widget.edit_separator()
                text_widget.tag_remove("sel", "1.0", "end")
                if selection:
                    text_widget.tag_add("sel", "format_start", "format_end")
                editor.refresh()
            close()

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        apply_button = ttk.Button(buttons, text="Apply Formatting", command=apply)
        apply_button.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=close).pack(side="right", padx=8)
        window.protocol("WM_DELETE_WINDOW", close)
        window.bind("<Escape>", lambda event: close())
        operation.trace_add("write", refresh)
        width.trace_add("write", refresh)
        refresh()

    api.add_command("Text Formatting", open_formatter)
