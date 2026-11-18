"""Pocket Editor project tree. File operations require explicit user actions."""
import json
import os
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


def valid_name(value):
    return bool(value and value.strip() and value not in (".", "..")
                and not any(character in value for character in '/\\\0')
                and Path(value).name == value)


def contains(parent, child):
    if child is None:
        return False
    try:
        Path(child).absolute().relative_to(parent.absolute())
        return True
    except ValueError:
        return False


def register(api):
    editor = api._editor
    root = editor.root
    project = None
    project_file = None
    paths = {}
    visible = False
    sidebar = ttk.Frame(editor.workspace, padding=(14, 14), width=260)
    header = ttk.Frame(sidebar)
    header.pack(fill="x", pady=(0, 8))
    heading = ttk.Label(header, text="PROJECT", anchor="w", style="Caption.TLabel")
    heading.pack(side="left", fill="x", expand=True)
    ttk.Button(header, text="Hide", width=5, command=lambda: toggle()).pack(side="right")
    controls = ttk.Frame(sidebar)
    controls.pack(fill="x", pady=(0, 6))
    tools = ttk.Frame(sidebar)
    tools.pack(fill="x", pady=(0, 8))
    frame = ttk.Frame(sidebar)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, show="tree", selectmode="browse")
    scrollbar = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    status = ttk.Label(sidebar, text="Choose a project folder.", wraplength=240, style="Muted.TLabel")
    status.pack(fill="x", pady=(6, 0))

    def notify(message):
        status.configure(text=message)

    def failure(error):
        messagebox.showerror("Project Explorer", str(error), parent=root)

    def toggle():
        nonlocal visible
        if visible:
            sidebar.pack_forget()
        else:
            sidebar.pack(side="left", fill="y", before=editor.editor_panel)
        visible = not visible

    def add_node(parent, path):
        suffix = "/" if path.is_dir() else ""
        if path.is_symlink():
            suffix += " ↗"
        item = tree.insert(parent, "end", text=path.name + suffix)
        paths[item] = path
        if path.is_dir() and not path.is_symlink():
            tree.insert(item, "end", text="Loading…")
        return item

    def populate(item):
        path = paths.get(item)
        if path is None or not path.is_dir() or path.is_symlink():
            return
        try:
            children = sorted(path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.casefold()))
        except OSError as error:
            failure(error)
            return

        def forget(node):
            for child in tree.get_children(node):
                forget(child)
                paths.pop(child, None)

        forget(item)
        for child in tree.get_children(item):
            tree.delete(child)
        for child in children:
            add_node(item, child)

    def refresh():
        paths.clear()
        for item in tree.get_children():
            tree.delete(item)
        if project is None:
            return
        item = tree.insert("", "end", text=project.name or str(project), open=True)
        paths[item] = project
        populate(item)
        tree.selection_set(item)

    def activate_project(directory, filename=None):
        nonlocal project, project_file
        directory = Path(directory).resolve()
        if not directory.is_dir():
            raise ValueError(f"Project folder does not exist: {directory}")
        # Check access before replacing the current project.
        list(directory.iterdir())
        project, project_file = directory, filename
        heading.configure(text=project.name or str(project))
        refresh()
        if not visible:
            toggle()
        notify(str(project))
        if hasattr(api, "emit"):
            api.emit("project_changed", action="opened", path=str(project))

    def choose_project():
        directory = filedialog.askdirectory(parent=root, title="Open Project Folder")
        if directory:
            try:
                activate_project(directory)
            except (OSError, ValueError) as error:
                failure(error)

    project_types = [("Pocket Editor Project", "*.pocket-project"), ("All files", "*")]

    def load_project():
        filename = filedialog.askopenfilename(parent=root, title="Load Project",
                                              filetypes=project_types)
        if not filename:
            return
        try:
            filename = Path(filename).resolve()
            data = json.loads(filename.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("format") != "pocket-editor-project"
                    or type(data.get("version")) is not int or data["version"] != 1
                    or not isinstance(data.get("folder"), str) or not data["folder"].strip()):
                raise ValueError("Invalid or unsupported Pocket Editor project file.")
            activate_project(filename.parent / data["folder"], filename)
        except (OSError, ValueError) as error:
            failure(error)

    def save_project(save_as=False):
        nonlocal project_file
        if project is None:
            choose_project()
        if project is None:
            return
        filename = project_file
        if save_as or filename is None:
            chosen = filedialog.asksaveasfilename(
                parent=root, title="Save Project", defaultextension=".pocket-project",
                initialdir=str(project), initialfile=project.name + ".pocket-project",
                filetypes=project_types)
            if not chosen:
                return
            filename = Path(chosen).resolve()
        try:
            try:
                folder = os.path.relpath(project, filename.parent)
            except ValueError:  # Different drives on Windows.
                folder = str(project)
            data = {"format": "pocket-editor-project", "version": 1, "folder": folder}
            editor.write_data(filename, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
        except (OSError, ValueError) as error:
            failure(error)
            return
        project_file = filename
        notify(f"Saved project: {filename.name}")

    def selected():
        selection = tree.selection()
        return paths.get(selection[0]) if selection else None

    def destination():
        path = selected() or project
        if path is None:
            choose_project()
            path = project
        if path is None:
            return None
        if path.is_symlink():
            failure("Select a folder within the project, rather than a symbolic link.")
            return None
        return path if path.is_dir() else path.parent

    def ask_name(title, initial=""):
        value = simpledialog.askstring(title, "Name", initialvalue=initial, parent=root)
        if value is None:
            return None
        if not valid_name(value):
            failure("Enter a single file or folder name without path separators.")
            return None
        return value

    def create(folder=False):
        parent = destination()
        if parent is None:
            return
        name = ask_name("New Folder" if folder else "New File")
        if name is None:
            return
        path = parent / name
        try:
            if folder:
                path.mkdir()
            else:
                with path.open("x", encoding="utf-8"):
                    pass
        except OSError as error:
            failure(error)
            return
        refresh()
        notify(f"Created {path.relative_to(project)}")
        if hasattr(api, "emit"):
            api.emit("project_changed", action="created", path=str(path))

    def open_selected(_event=None):
        path = selected()
        if path is None or path.is_dir():
            return
        editor.open_path(path)
        notify(f"Opened {path.name}")
        if hasattr(api, "emit"):
            api.emit("document_changed")

    def rename():
        path = selected()
        if path is None or path == project:
            failure("Select a file or subfolder to rename.")
            return
        name = ask_name("Rename", path.name)
        if name is None or name == path.name:
            return
        target = path.with_name(name)
        if os.path.lexists(target):
            failure("An item with that name already exists.")
            return
        if not messagebox.askyesno("Rename", f"Rename {path.name} to {name}?", parent=root):
            return
        editor.remember_document()
        was_symlink = path.is_symlink()
        try:
            path.rename(target)
        except OSError as error:
            failure(error)
            return
        for document in editor.documents:
            current_path = document["path"]
            matches = (path.absolute() == Path(current_path).absolute() if was_symlink and current_path
                       else contains(path, current_path) and not was_symlink)
            if matches:
                document["path"] = target / Path(current_path).absolute().relative_to(path.absolute())
        editor.path = editor.active_document["path"]
        editor.refresh()
        editor.document_changed()
        refresh()
        notify(f"Renamed {path.name} to {name}")
        if hasattr(api, "emit"):
            api.emit("project_changed", action="renamed", path=str(target))
            api.emit("document_changed")

    def delete():
        path = selected()
        if path is None or path == project:
            failure("Select a file or subfolder to delete.")
            return
        folder = path.is_dir() and not path.is_symlink()
        detail = " and all its contents" if folder else ""
        if not messagebox.askyesno("Permanently Delete",
                                  f"Permanently delete {path.name}{detail}?\nThis cannot be undone.",
                                  icon="warning", parent=root):
            return
        editor.remember_document()
        affected_documents = [document for document in editor.documents if contains(path, document["path"])]
        previous = editor.active_document
        for document in affected_documents:
            editor.select_document(document)
            if not editor.may_discard():
                editor.select_document(previous)
                return
        editor.select_document(previous)
        try:
            if folder:
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as error:
            failure(error)
            refresh()
            return
        for document in affected_documents:
            document["path"] = None
            document["widget"].edit_modified(True)
        editor.path = editor.active_document["path"]
        editor.refresh()
        refresh()
        notify(f"Deleted {path.name}")
        if hasattr(api, "emit"):
            api.emit("project_changed", action="deleted", path=str(path))

    def project_tool(service):
        if project is None:
            choose_project()
        if project is None:
            return
        try:
            api.call_service(service, project)
        except (LookupError, ValueError, OSError) as error:
            failure(error)

    menu = tk.Menu(tree, tearoff=False)
    for label, command in (("Open Project Folder…", choose_project),
                           ("Load Project…", load_project), ("Save Project", save_project),
                           ("Save Project As…", lambda: save_project(True)),
                           ("Git Project…", lambda: project_tool("git_project.open_project")),
                           ("Team Project…", lambda: project_tool("team_project.open_project")),
                           ("Open File", open_selected), ("New File…", create),
                           ("New Folder…", lambda: create(True)), ("Rename…", rename),
                           ("Delete…", delete), ("Refresh", refresh)):
        menu.add_command(label=label, command=command)

    def context(event):
        item = tree.identify_row(event.y)
        if item:
            tree.selection_set(item)
            tree.focus(item)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    for column, (label, command) in enumerate((
            ("Open", choose_project), ("Save", save_project), ("Load", load_project))):
        controls.columnconfigure(column, weight=1, uniform="project_actions")
        ttk.Button(controls, text=label, width=6, command=command).grid(
            row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
    ttk.Button(tools, text="Actions ▾", command=lambda: menu.tk_popup(
        tools.winfo_rootx(), tools.winfo_rooty() + tools.winfo_height())).pack(side="right")
    tree.bind("<<TreeviewOpen>>", lambda event: populate(tree.focus()))
    tree.bind("<Double-1>", open_selected)
    tree.bind("<Return>", open_selected)
    tree.bind("<Button-3>", context)
    tree.bind("<F2>", lambda event: rename())
    if hasattr(api, "provide_service"):
        api.provide_service("get_project", lambda: project)
        api.provide_service("open_project", activate_project)
        api.on("project_changed", lambda source, **payload: refresh()
               if source != api.plugin_id else None)
    api.on_cleanup(sidebar.destroy)
    api.add_command("Project Explorer", toggle)
    api.add_command("Open Project", choose_project)
    api.add_command("Load Project", load_project)
    api.add_command("Save Project", save_project)
    api.add_command("Save Project As", lambda: save_project(True))
