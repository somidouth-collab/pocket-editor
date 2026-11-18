"""Pocket Editor plugin: editor themes and syntax highlighting."""
import io
import json
import keyword
import re
import tokenize
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

THEMES = {
    "Dark": ("#1e1e1e", "#cccccc", "#264f78", "#858585", "#569cd6", "#ce9178", "#6a9955", "#b5cea8", "#dcdcaa"),
    "Light": ("#ffffff", "#24292f", "#b6d7ff", "#6e7781", "#0550ae", "#0a3069", "#6e7781", "#953800", "#8250df"),
    "Dracula": ("#282a36", "#f8f8f2", "#44475a", "#6272a4", "#ff79c6", "#f1fa8c", "#6272a4", "#bd93f9", "#50fa7b"),
    "Solarized Dark": ("#002b36", "#839496", "#073642", "#586e75", "#859900", "#2aa198", "#586e75", "#d33682", "#b58900"),
}
LANGUAGES = ("Auto", "Python", "JavaScript", "C/C++", "JSON", "PHP", "Plain text")
EXTENSIONS = {".py": "Python", ".pyw": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
              ".ts": "JavaScript", ".tsx": "JavaScript", ".c": "C/C++", ".h": "C/C++",
              ".cpp": "C/C++", ".hpp": "C/C++", ".cc": "C/C++", ".json": "JSON", ".php": "PHP", ".phtml": "PHP", ".php8": "PHP"}
WORDS = set(("if else for while do break continue return switch case default class struct enum "
             "public private protected static const let var function async await new delete try catch "
             "finally throw import export from extends implements interface typeof instanceof void "
             "int float double char bool long short unsigned signed auto namespace using include "
             "true false null undefined this super sizeof typedef volatile virtual override namespace use require require_once include_once echo elseif endif endforeach endfor endwhile fn match readonly trait final abstract public protected private extends implements instanceof global isset unset empty array as yield die exit declare").split())
PATTERN = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)|'
    r'(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`)|'
    r'(?P<number>\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b)|'
    r'(?P<word>\b[A-Za-z_$][\w$]*\b)'
)


def spans(source, language):
    if language == "Python":
        lines = source.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        try:
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                kind = {tokenize.STRING: "string", tokenize.COMMENT: "comment",
                        tokenize.NUMBER: "number"}.get(token.type)
                if token.type == tokenize.NAME and keyword.iskeyword(token.string):
                    kind = "keyword"
                if kind:
                    yield kind, offsets[token.start[0] - 1] + token.start[1], offsets[token.end[0] - 1] + token.end[1]
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
    elif language != "Plain text":
        for match in PATTERN.finditer(source):
            kind = match.lastgroup
            if kind == "word":
                if match.group() not in ({"true", "false", "null"} if language == "JSON" else WORDS):
                    continue
                kind = "keyword"
            if language == "JSON" and kind == "comment":
                continue
            yield kind, match.start(), match.end()


def register(api):
    editor = api._editor
    text = editor.buffer
    root = editor.root
    settings_path = Path(__file__).with_name("code_theme_settings.json")
    saved = {}
    try:
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            saved = {}
    except (OSError, ValueError):
        pass
    theme = tk.StringVar(root, saved.get("theme") if saved.get("theme") in THEMES else "Dark")
    language = tk.StringVar(root, saved.get("language") if saved.get("language") in LANGUAGES else "Auto")
    pending = None
    window = None
    last_source = None
    last_language = None

    def highlight():
        nonlocal pending, last_source, last_language
        pending = None
        source = api.get_text()
        chosen = language.get()
        if chosen == "Auto":
            path = api.get_path()
            chosen = EXTENSIONS.get(path.suffix.lower(), "Plain text") if path else "Plain text"
        if (source, chosen) == (last_source, last_language):
            return
        last_source, last_language = source, chosen
        for kind in ("keyword", "string", "comment", "number"):
            text.tag_remove("syntax_" + kind, "1.0", "end")
        # Bound work so large documents remain usable.
        if len(source) > 300000:
            return
        ranges = {}
        for kind, start, end in spans(source, chosen):
            ranges.setdefault(kind, []).extend((f"1.0 +{start}c", f"1.0 +{end}c"))
        for kind, indexes in ranges.items():
            for offset in range(0, len(indexes), 1000):
                text.tag_add("syntax_" + kind, *indexes[offset:offset + 1000])
        text.tag_raise("sel")

    def schedule(_event=None):
        nonlocal pending
        if pending is not None:
            root.after_cancel(pending)
        pending = root.after(180, highlight)

    def apply(save=True):
        nonlocal last_source
        bg, fg, selection, muted, kw, string, comment, number, _ = THEMES[theme.get()]
        text.configure(background=bg, foreground=fg, insertbackground=fg,
                       selectbackground=selection, selectforeground=fg,
                       inactiveselectbackground=selection)
        editor.gutter.configure(background=bg)
        editor.gutter_ink = muted
        for kind, color in (("keyword", kw), ("string", string), ("comment", comment), ("number", number)):
            name = "syntax_" + kind
            text.tag_configure(name, foreground=color)
            text.tag_lower(name)
        editor.queue_gutter()
        last_source = None
        schedule()
        if save:
            try:
                editor.write_data(settings_path, json.dumps({"theme": theme.get(), "language": language.get()},
                                                       indent=2).encode("utf-8"))
            except OSError as error:
                messagebox.showerror("Theme settings", str(error), parent=root)

    def show():
        nonlocal window
        if window is not None and window.winfo_exists():
            window.lift()
            return
        window = tk.Toplevel(root)
        window.title("Code Theme")
        window.transient(root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        for row, (label, variable, choices) in enumerate((
            ("Editor theme", theme, tuple(THEMES)), ("Language", language, LANGUAGES)
        )):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=8)
            combo = ttk.Combobox(body, textvariable=variable, values=choices, state="readonly", width=24)
            combo.grid(row=row, column=1, pady=8)
            combo.bind("<<ComboboxSelected>>", lambda event: apply())
        ttk.Label(body, text="Auto uses the filename extension. Choose a language for unsaved code.\n"
                            "Highlighting pauses above 300,000 characters.").grid(
                                row=2, column=0, columnspan=2, pady=12, sticky="w")
        ttk.Button(body, text="Close", command=window.destroy).grid(row=3, column=1, sticky="e")

    text.bind("<KeyRelease>", schedule, add="+")
    text.bind("<<Modified>>", schedule, add="+")
    text.bind("<<Paste>>", schedule, add="+")
    text.bind("<<Cut>>", schedule, add="+")
    text.bind("<FocusIn>", schedule, add="+")
    # Save As may change the detected language without changing document text.
    root.bind("<ButtonRelease-1>", schedule, add="+")
    if hasattr(api, "on"):
        api.on("document_switched", lambda **event: apply(save=False))
        api.on("document_changed", lambda **event: schedule())
        api.on("document_formatted", lambda **event: schedule())
        api.provide_service("refresh", schedule)
    api.add_command("Code Theme", show)
    apply(save=False)
