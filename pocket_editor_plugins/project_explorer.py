"""Pocket Editor project tree. File operations require explicit user actions."""
from pocket_editor_plugins._explorer_icons import IconButton
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
    folder_icon = tk.PhotoImage(master=root,width=16,height=16)
    folder_icon.put('#71869a',to=(1,4,15,14));folder_icon.put('#11161c',to=(2,5,14,13));folder_icon.put('#71869a',to=(2,2,7,5))
    file_icon = tk.PhotoImage(master=root,width=16,height=16)
    file_icon.put('#71869a',to=(4,1,13,15));file_icon.put('#11161c',to=(5,2,12,14))

    visible = False
    menu = None
    refresh_pending = False
    state_path = editor.settings_path.with_name('project_explorer_settings.json')
    last_state = None
    try:
        candidate=json.loads(state_path.read_text(encoding='utf-8'))
        if isinstance(candidate,dict):last_state=candidate
    except (OSError,ValueError):pass

    sidebar = ttk.Frame(editor.workspace, padding=(14, 14), width=260)
    sidebar._tree_icons = (folder_icon,file_icon)
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
        if hasattr(editor, "plugin_sidebar"):
            editor.plugin_sidebar.toggle(api.plugin_id)
            return
        if visible:
            sidebar.pack_forget()
        else:
            sidebar.pack(side="left", fill="y", before=editor.editor_panel)
        visible = not visible

    def add_node(parent, path):
        suffix = "/" if path.is_dir() else ""
        if path.is_symlink():
            suffix += " ↗"
        item = tree.insert(parent, "end", text=path.name + suffix, image=folder_icon if path.is_dir() else file_icon)
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
        nonlocal refresh_pending
        if menu is not None and menu.winfo_ismapped():
            refresh_pending=True
            return
        paths.clear()
        for item in tree.get_children():
            tree.delete(item)
        if project is None:
            return
        item = tree.insert("", "end", text=project.name or str(project), image=folder_icon, open=True)
        paths[item] = project
        populate(item)
        tree.selection_set(item)

    def activate_project(directory, filename=None, reveal=True, persist=True):
        nonlocal project, project_file
        directory = Path(directory).resolve()
        if not directory.is_dir():
            raise ValueError(f"Project folder does not exist: {directory}")
        # Check access before replacing the current project.
        list(directory.iterdir())
        if menu is not None and menu.winfo_ismapped():menu.unpost()
        project, project_file = directory, filename
        heading.configure(text=project.name or str(project))
        refresh()
        if reveal and hasattr(editor, "plugin_sidebar"):
            editor.plugin_sidebar.open(api.plugin_id)
        elif reveal and not visible:
            toggle()
        notify(str(project))
        if persist:save_state()
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
        if path is None: return
        if path.is_dir():
            item = tree.focus()
            if item:
                opened = bool(tree.item(item, 'open'))
                if not opened: populate(item)
                tree.item(item, open=not opened)
            return "break"
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

    def get_state():
        if project is None:return {}
        expanded=[]
        for item,path in paths.items():
            if tree.exists(item) and tree.item(item,'open'):
                try:expanded.append(path.relative_to(project).as_posix())
                except ValueError:pass
                if len(expanded)>=500:break
        path=selected()
        return {'project':str(project),'project_file':str(project_file) if project_file else None,
                'expanded':expanded,'selected':path.relative_to(project).as_posix() if path else None,
                'view':tree.yview()[0]}

    def save_state():
        state=get_state()
        if state:
            try:editor.write_data(state_path,json.dumps(state,ensure_ascii=False).encode('utf-8'))
            except OSError as error:notify('Could not save Explorer state: '+str(error))

    def restore_state(state, reveal=False):
        if not isinstance(state,dict) or not isinstance(state.get('project'),str):return False
        directory=Path(state['project']).expanduser().resolve()
        if not directory.is_dir():
            notify('Last project is unavailable: '+str(directory));return False
        filename=state.get('project_file')
        filename=Path(filename) if isinstance(filename,str) and Path(filename).is_file() else None
        if directory!=project:activate_project(directory,filename,reveal=reveal,persist=False)
        expanded=state.get('expanded',[])
        if not isinstance(expanded,list):expanded=[]
        for relative in sorted((s for s in expanded[:500] if isinstance(s,str)),key=lambda s:len(Path(s).parts)):
            rel=Path(relative)
            if rel.is_absolute() or '..' in rel.parts:continue
            path=project/rel
            item=next((item for item,value in paths.items() if value==path),None)
            if item and path.is_dir() and not path.is_symlink():
                if not tree.item(item,'open'):populate(item)
                tree.item(item,open=True)
        selection=state.get('selected')
        if isinstance(selection,str):
            rel=Path(selection)
            if not rel.is_absolute() and '..' not in rel.parts:
                item=next((item for item,value in paths.items() if value==project/rel),None)
                if item:tree.selection_set(item);tree.focus(item)
        try:tree.yview_moveto(max(0,min(1,float(state.get('view',0)))))
        except (TypeError,ValueError):pass
        return True

    restored=False
    def restore_last_project(**payload):
        nonlocal restored
        if not restored:
            restored=True
            if last_state:restore_state(last_state)

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
                           ("Open", open_selected), ("New File…", create),
                           ("New Folder…", lambda: create(True)), ("Rename…", rename),
                           ("Delete…", delete), ("Refresh", refresh)):
        menu.add_command(label=label, command=command)

    def copy_path():
        path = selected()
        if path is not None:
            root.clipboard_clear();root.clipboard_append(str(path))
    menu.add_command(label='Copy Path',command=copy_path)

    def update_menu():
        path = selected()
        for label,enabled in [('Open',path is not None),
                              ('New File…',project is not None),('New Folder…',project is not None),
                              ('Rename…',path is not None and path != project),
                              ('Delete…',path is not None and path != project),
                              ('Copy Path',path is not None)]:
            menu.entryconfigure(label,state='normal' if enabled else 'disabled')

    def popup(x,y):
        update_menu()
        try:menu.tk_popup(x+3,y+3)
        except tk.TclError:
            menu.grab_release()
            raise

    def menu_closed(event=None):
        nonlocal refresh_pending
        try:menu.grab_release()
        except tk.TclError:pass
        if refresh_pending:
            refresh_pending=False
            root.after_idle(refresh)
    menu.bind('<Unmap>',menu_closed)

    def context(event):
        item = tree.identify_row(event.y)
        if item:
            tree.selection_set(item);tree.focus(item)
        else:
            tree.selection_remove(tree.selection());tree.focus('')
        popup(event.x_root,event.y_root)
        return 'break'

    def keyboard_context(event=None):
        item = tree.focus()
        if item:tree.selection_set(item)
        box=tree.bbox(item) if item else ()
        popup(tree.winfo_rootx()+20,tree.winfo_rooty()+(box[1]+box[3] if box else 20))
        return 'break'

    def actions():
        popup(controls.winfo_rootx(),controls.winfo_rooty()+controls.winfo_height())

    tools.pack_forget()
    for action,label,command in [
        ('open','Open project folder',choose_project),('save','Save project',save_project),
        ('load','Load project',load_project),('new_file','New file',create),
        ('new_folder','New folder',lambda:create(True)),('refresh','Refresh tree',refresh),
        ('more','More project and item actions',actions)]:
        IconButton(controls,action,label,command).pack(side='left',padx=(0,4))
    tree.bind("<<TreeviewOpen>>", lambda event: populate(tree.focus()))
    tree.bind("<Double-1>", open_selected)
    tree.bind("<Return>", open_selected)
    def select_context_target(event):
        item=tree.identify_row(event.y)
        if item:tree.selection_set(item);tree.focus(item)
        else:tree.selection_remove(tree.selection());tree.focus('')
        return 'break'
    tree.bind("<Button-3>", select_context_target)
    tree.bind("<ButtonRelease-3>", context)
    tree.bind("<Control-Button-1>", select_context_target)
    tree.bind("<Control-ButtonRelease-1>", context)
    tree.bind("<Shift-F10>", keyboard_context)
    tree.bind("<Menu>", keyboard_context)
    tree.bind("<F2>", lambda event: rename())
    if hasattr(api, "provide_service"):
        api.provide_service("get_project", lambda: project)
        api.provide_service("get_state",get_state)
        api.provide_service("restore_state",restore_state)
        api.on("plugins_loaded",lambda **payload:restore_last_project())
        api.provide_service("open_project", activate_project)
        api.on("project_changed", lambda source, **payload: refresh()
               if source != api.plugin_id else None)
    def mount(parent):
        header.pack_forget()
        sidebar.pack(in_=parent, fill="both", expand=True)
        return sidebar
    api.add_panel(mount, width=320)
    def cleanup():
        save_state()
        sidebar.destroy()
    api.on_cleanup(cleanup)
    api.add_command("Project Explorer", toggle)
    api.add_command("Open Project", choose_project)
    api.add_command("Load Project", load_project)
    api.add_command("Save Project", save_project)
    api.add_command("Save Project As", lambda: save_project(True))
