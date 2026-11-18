"""Pocket Editor rich-text plugin. Install in pocket_editor_plugins."""
import json
import tkinter as tk
from tkinter import colorchooser, filedialog, font, messagebox, ttk


def register(api):
    editor = api._editor
    text = editor.buffer
    root = editor.root
    # Use the core atomic writer without importing a second copy of the running editor.
    editor_atomic_write = editor.write_data
    fonts = []
    styles = {}
    window = None

    def style_tag(options):
        key = json.dumps(options, sort_keys=True)
        if key in styles and styles[key] in text.tag_names():
            return styles[key]
        name = styles.get(key, f"rich_{len(styles)}")
        config = dict(options)
        font_options = config.pop("font_spec", None)
        if font_options:
            instance = font.Font(root=root, **font_options)
            fonts.append(instance)
            config["font"] = instance
        text.tag_configure(name, **config)
        styles[key] = name
        return name

    def apply_style(options, paragraph=False):
        selection = text.tag_ranges("sel")
        if not selection:
            messagebox.showinfo("Select text", "Select the text to format first.", parent=root)
            return
        start, end = map(str, selection)
        if paragraph:
            start = text.index(f"{start} linestart")
            last = text.index(f"{end} -1c")
            end = text.index(f"{last} lineend +1c")
        name = style_tag(options)
        text.tag_add(name, start, end)
        text.tag_raise(name)
        text.tag_raise("sel")
        editor.rich_dirty = True
        text.edit_modified(True)
        editor.refresh()

    def font_style(change):
        selection = text.tag_ranges("sel")
        if not selection:
            apply_style({})
            return
        start, end = map(str, selection)
        # Resolve each character's effective font so partial changes preserve
        # other attributes and differently formatted spans.
        spans = []
        cursor = start
        while text.compare(cursor, "<", end):
            effective = editor.editor_font.actual()
            for tag in text.tag_names(cursor):
                value = text.tag_cget(tag, "font")
                if value:
                    effective = font.Font(root=root, font=value).actual()
            effective.update(change)
            following = text.index(f"{cursor} +1c")
            if spans and spans[-1][2] == effective:
                spans[-1] = (spans[-1][0], following, effective)
            else:
                spans.append((cursor, following, effective))
            cursor = following
        for begin, finish, spec in spans:
            name = style_tag({"font_spec": spec})
            text.tag_add(name, begin, finish)
            text.tag_raise(name)
        text.tag_raise("sel")
        editor.rich_dirty = True
        text.edit_modified(True)
        editor.refresh()

    def choose_color(property_name):
        color = colorchooser.askcolor(parent=root)[1]
        if color:
            apply_style({property_name: color})

    def save_rich():
        path = filedialog.asksaveasfilename(parent=root, defaultextension=".pocket",
                                            filetypes=[("Pocket formatted document", "*.pocket")])
        if not path:
            return
        spans = []
        # Include restored tags, which are not in this plugin instance's registry.
        for name in text.tag_names():
            ranges = list(map(str, text.tag_ranges(name)))
            if not name.startswith("rich_") or not ranges:
                continue
            options = {key: value[-1] for key, value in text.tag_configure(name).items() if value[-1] != ""}
            if options.get("font"):
                options["font_spec"] = font.Font(root=root, font=options.pop("font")).actual()
            spans.append({"style": options, "ranges": ranges})
        payload = {"format": "pocket-rich-1", "text": api.get_text(), "styles": spans}
        try:
            editor_atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        except OSError as error:
            messagebox.showerror("Save failed", str(error), parent=root)
            return
        editor.rich_dirty = False
        editor.refresh()
        messagebox.showinfo("Formatted copy saved", "Text and formatting were saved to the .pocket file.",
                            parent=root)

    def open_rich():
        path = filedialog.askopenfilename(parent=root,
                                          filetypes=[("Pocket formatted document", "*.pocket")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as stream:
                payload = json.load(stream)
            if payload.get("format") != "pocket-rich-1" or not isinstance(payload["text"], str):
                raise ValueError("Unsupported document format")
            # Validate styles on a separate widget before changing the document.
            probe = tk.Text(root)
            try:
                probe.insert("1.0", payload["text"])
                for number, span in enumerate(payload["styles"]):
                    options = dict(span["style"])
                    if set(options) - {"font_spec", "foreground", "background", "justify"}:
                        raise ValueError("Unsupported formatting option")
                    if not isinstance(span["ranges"], list) or not all(isinstance(index, str) for index in span["ranges"]):
                        raise ValueError("Invalid formatting ranges")
                    spec = options.pop("font_spec", None)
                    if spec:
                        options["font"] = font.Font(root=root, **spec)
                    probe.tag_configure(str(number), **options)
                    ranges = span["ranges"]
                    if len(ranges) % 2:
                        raise ValueError("Invalid formatting range")
                    for index in ranges:
                        probe.index(index)
            finally:
                probe.destroy()
        except (OSError, ValueError, KeyError, TypeError, AttributeError, tk.TclError) as error:
            messagebox.showerror("Open failed", str(error), parent=root)
            return
        editor.new()
        editor.set_document(None, payload["text"])
        for span in payload["styles"]:
            name = style_tag(span["style"])
            for start, end in zip(span["ranges"][::2], span["ranges"][1::2]):
                text.tag_add(name, start, end)
            text.tag_raise(name)
        text.tag_raise("sel")
        editor.rich_dirty = True
        text.edit_modified(True)
        editor.refresh()

    def clear_style():
        selection = text.tag_ranges("sel")
        if not selection:
            return
        for name in styles.values():
            text.tag_remove(name, *selection)
        editor.rich_dirty = True
        text.edit_modified(True)
        editor.refresh()

    def show():
        nonlocal window
        if window is not None and window.winfo_exists():
            window.lift()
            return
        window = tk.Toplevel(root)
        window.title("Rich Text Formatting")
        window.transient(root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Select text in the editor, then apply formatting.").pack(anchor="w")
        typography = ttk.LabelFrame(body, text="Font", padding=10)
        typography.pack(fill="x", pady=10)
        family = tk.StringVar(window, editor.editor_font.cget("family"))
        size = tk.StringVar(window, str(editor.editor_font.cget("size")))
        ttk.Combobox(typography, textvariable=family, values=sorted(set(font.families(root))),
                     state="readonly", width=28).grid(row=0, column=0, columnspan=3)
        ttk.Spinbox(typography, from_=6, to=96, textvariable=size, width=5).grid(row=0, column=3)

        def apply_font():
            try:
                points = int(size.get())
                if not 6 <= points <= 96:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Font size", "Enter a size from 6 to 96.", parent=window)
                return
            font_style({"family": family.get(), "size": points})

        ttk.Button(typography, text="Apply Font", command=apply_font).grid(row=0, column=4, padx=6)
        for col, (label, change) in enumerate((
            ("Bold", {"weight": "bold"}), ("Italic", {"slant": "italic"}),
            ("Underline", {"underline": 1}), ("Strikeout", {"overstrike": 1}),
            ("Normal", {"weight": "normal", "slant": "roman", "underline": 0, "overstrike": 0}),
        )):
            ttk.Button(typography, text=label, command=lambda change=change: font_style(change)).grid(
                row=1, column=col, pady=(8, 0), padx=2)
        colors = ttk.Frame(body)
        colors.pack(fill="x")
        ttk.Button(colors, text="Text Color…", command=lambda: choose_color("foreground")).pack(side="left")
        ttk.Button(colors, text="Highlight…", command=lambda: choose_color("background")).pack(side="left", padx=6)
        ttk.Button(colors, text="Clear Formatting", command=clear_style).pack(side="left")
        paragraph = ttk.LabelFrame(body, text="Paragraph alignment", padding=10)
        paragraph.pack(fill="x", pady=10)
        for label, alignment in (("Left", "left"), ("Center", "center"), ("Right", "right")):
            ttk.Button(paragraph, text=label,
                       command=lambda alignment=alignment: apply_style(
                           {"justify": alignment}, paragraph=True)).pack(side="left", padx=3)
        files = ttk.Frame(body)
        files.pack(fill="x")
        ttk.Button(files, text="Open Formatted…", command=open_rich).pack(side="left")
        ttk.Button(files, text="Save Formatted Copy…", command=save_rich).pack(side="left", padx=6)
        ttk.Label(body, text="Use .pocket files to keep styles. File → Save writes plain text.\nFormatting changes cannot be undone; text edits can.").pack(
            anchor="w", pady=(12, 0))

    # Keep the document selection visible while using this floating toolbar.
    text.configure(exportselection=False)
    api.add_command("Rich Text", show)
