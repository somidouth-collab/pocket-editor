"""Interactive project terminal and command runner with one integrated menu bar."""
from pocket_editor_plugins._docking import create_window
from pocket_editor_plugins._terminal_view import InteractiveTerminal
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, font, messagebox, ttk
from pocket_editor_plugins._terminal_colors import AnsiDecoder, DEFAULTS, PALETTE, PRESETS, classify_line, color_value, validate_settings


SETTINGS_PATH = Path(__file__).with_name("project_terminal_settings.json")

def register(api):
    root = api._editor.root
    config_path = SETTINGS_PATH
    configs = {}
    try:
        configs = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(configs, dict):
            configs = {}
    except (OSError, ValueError):
        pass
    try:
        appearance = validate_settings(configs.get("_appearance", {}))
    except ValueError:
        appearance = validate_settings({})
    decoder = AnsiDecoder()
    style_tags = {}
    terminal_font = font.nametofont("TkFixedFont", root=root).copy()
    bold_font = terminal_font.copy()
    settings_window = None
    window = None
    process = None
    messages = queue.Queue(maxsize=128)
    shutting_down = threading.Event()
    folder_var = None
    command_var = None
    output = None
    status = None
    running = False
    poll_job = None
    interactive = None
    view_mode = None
    runner_frame = None
    command_dialog = None
    shell_var = tk.StringVar(root, configs.get("_shell", "") if isinstance(configs.get("_shell", ""), str) else "")

    def project_directory():
        try:
            project = api.call_service("project_explorer.get_project")
            if project and Path(project).is_dir():
                return str(project)
        except (AttributeError, LookupError):
            pass
        path = api.get_path()
        return str(path.parent) if path and path.parent.is_dir() else str(Path.cwd())

    def load_project(directory):
        folder_var.set(directory)
        entry = configs.get(directory, {})
        command_var.set(entry.get("command", "") if isinstance(entry, dict) else "")

    def configure_style(tag, style):
        if not appearance["ansi"]:
            output.tag_configure(tag, foreground="", background="", underline=False, font="")
            return
        fg, bg, bold, underline, reverse = style
        fg = color_value(fg, appearance, "foreground")
        bg = color_value(bg, appearance, "background")
        if reverse: fg, bg = bg, fg
        output.tag_configure(tag, foreground=fg, background=bg, underline=underline,
                             font=bold_font if bold else terminal_font)

    def apply_appearance():
        if interactive is not None:interactive.apply_appearance(appearance)
        available = set(font.families(root))
        family = appearance["font_family"]
        if family not in available:
            family = font.nametofont("TkFixedFont", root=root).cget("family")
        terminal_font.configure(family=family, size=appearance["font_size"])
        bold_font.configure(family=family, size=appearance["font_size"], weight="bold")
        if output is not None and output.winfo_exists():
            output.configure(background=appearance["background"], foreground=appearance["foreground"],
                             insertbackground=appearance["foreground"], font=terminal_font)
            for name in ("command", "status", "error", "warning", "success"):
                output.tag_configure("terminal_" + name, foreground=appearance[name])
                output.tag_configure("terminal_auto_" + name, foreground=appearance[name])
                output.tag_lower("terminal_auto_" + name)
            categorize_lines(1, int(output.index("end-1c").split(".")[0]))
            for style, tag in style_tags.items(): configure_style(tag, style)

    def categorize_lines(first, last):
        for number in range(first, last + 1):
            start, end = f"{number}.0", f"{number}.end"
            for name in ("command", "status", "error", "warning", "success"):
                output.tag_remove("terminal_auto_" + name, start, end)
            if appearance["categorize"]:
                category = classify_line(output.get(start, end))
                if category:
                    output.tag_add("terminal_auto_" + category, start, end)

    def append(value, category=None, final=False):
        output.configure(state="normal")
        for text, style in decoder.feed(value, final=final):
            tag = "terminal_" + category if category else None
            if not category and appearance["ansi"] and style != (None, None, False, False, False):
                if style not in style_tags and len(style_tags) < 512:
                    tag = "terminal_ansi_" + str(len(style_tags))
                    style_tags[style] = tag
                    configure_style(tag, style)
                tag = style_tags.get(style)
            output.insert("end", text, (tag,) if tag else ())
        # Bound retained output so long-running jobs cannot exhaust GUI memory.
        output.delete("1.0", "end-1c -200000c")
        # Tk layout gets very expensive for huge unbroken lines, even without wrap.
        # Retain the tail of each long output line as well as bounding total output.
        last = output.index("end-1c linestart")
        first = output.index("end-1c -" + str(len(value)) + "c linestart")
        line = int(first.split(".")[0])
        final_line = int(last.split(".")[0])
        for number in range(line, final_line + 1):
            length = int(output.index(f"{number}.0 lineend").split(".")[1])
            if length > 8192:
                output.delete(f"{number}.0", f"{number}.0 lineend -8192c")
        if category is None:
            categorize_lines(line, final_line)
        output.see("end")
        output.configure(state="disabled")

    def save_config():
        directory = str(Path(folder_var.get()).expanduser().absolute())
        configs[directory] = {"command": command_var.get()}
        try:
            api._editor.write_data(config_path, json.dumps(configs, indent=2).encode("utf-8"))
        except OSError as error:
            messagebox.showerror("Command settings", str(error), parent=window)
            return
        status.set("Project command saved.")

    def stop():
        if process is None or not running:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            status.set("Stopping command…")
        except (OSError, ProcessLookupError) as error:
            status.set(str(error))

    def enqueue(item):
        while not shutting_down.is_set():
            try:
                messages.put(item, timeout=0.1)
                return
            except queue.Full:
                pass

    def cleanup():
        nonlocal poll_job
        shutting_down.set()
        if interactive is not None:interactive.cleanup()
        if poll_job and window is not None and window.winfo_exists():
            window.after_cancel(poll_job)
            poll_job = None
        stop()
        if process is not None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    api.on_cleanup(cleanup)

    def run():
        nonlocal process, running, decoder
        if running:
            status.set("Stop the running command before starting another.")
            return
        command = command_var.get().strip()
        directory = Path(folder_var.get()).expanduser()
        if not command or not directory.is_dir():
            messagebox.showerror("Run command", "Enter a command and an existing working directory.",
                                 parent=window)
            return
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        import locale
        encoding = locale.getpreferredencoding(False) if appearance["encoding"] == "locale" else appearance["encoding"]
        environment["PYTHONIOENCODING"] = encoding
        if appearance["force_color"]:
            environment.pop("NO_COLOR", None)
            environment.update(FORCE_COLOR="1", CLICOLOR_FORCE="1", TERM="xterm-256color")
        try:
            process = subprocess.Popen(
                command, shell=True, cwd=str(directory), env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except OSError as error:
            messagebox.showerror("Could not start command", str(error), parent=window)
            return
        running = True
        view_mode.set('runner');display_mode()
        decoder = AnsiDecoder()
        append(f"\n{directory}\n", category="status")
        append(f"$ {command}\n", category="command")
        status.set("Running…")
        current = process

        def read_output():
            import codecs
            decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
            try:
                while not shutting_down.is_set():
                    chunk = current.stdout.read1(4096)
                    if not chunk:
                        break
                    enqueue(("output", decoder.decode(chunk)))
                enqueue(("output", decoder.decode(b"", final=True)))
            finally:
                current.stdout.close()
                enqueue(("exit", current.wait()))

        threading.Thread(target=read_output, daemon=True).start()

    def poll():
        nonlocal running, poll_job
        poll_job = None
        if window is None or not window.winfo_exists():
            return
        for _ in range(8):
            try:
                kind, value = messages.get_nowait()
            except queue.Empty:
                break
            if kind == "output":
                append(value)
            else:
                running = False
                status.set(f"Finished with exit code {value}.")
                append("", final=True)
                decoder.style = (None, None, False, False, False)
                decoder.state = "text"
                append(f"\n[Exit code {value}]\n", category="success" if value == 0 else "error")
                if hasattr(api, "emit"):
                    api.emit("command_finished", exit_code=value)
        poll_job = window.after(80, poll)

    def destroy_window():
        nonlocal poll_job
        if getattr(window,'_docked_panel',False):
            if interactive is not None:interactive.stop()
            window.withdraw()
            return
        if interactive is not None:interactive.cleanup()
        if poll_job:
            window.after_cancel(poll_job)
            poll_job = None
        window.destroy()

    def close():
        if running:
            if not messagebox.askyesno("Command running", "Stop the command and close this window?",
                                      parent=window):
                return
            stop()
            # Keep polling until process output is drained.
            window.withdraw()
            def finish():
                if running:
                    window.after(100, finish)
                else:
                    destroy_window()
            finish()
        else:
            destroy_window()

    def display_mode():
        if interactive is None:return
        if view_mode.get()=='shell':
            runner_frame.pack_forget();interactive.frame.pack(fill='both',expand=True);interactive.resize();interactive.text.focus_set()
        else:
            interactive.frame.pack_forget();runner_frame.pack(fill='both',expand=True)

    def start_shell():
        if api._editor.closing:return
        if interactive is None or window is None or not window.winfo_exists():show();root.after(200,start_shell);return
        view_mode.set('shell');display_mode();interactive.start(folder_var.get())

    def run_command(command,directory=None):
        if api._editor.closing:return
        if folder_var is None or window is None or not window.winfo_exists():
            show();root.after(200,lambda:run_command(command,directory));return
        if directory:folder_var.set(str(directory))
        command_var.set(command);run()

    def command_settings():
        nonlocal command_dialog
        if window is None or not window.winfo_exists():show();root.after(200,command_settings);return
        if command_dialog is not None and command_dialog.winfo_exists():command_dialog.lift();return
        command_dialog=dialog=tk.Toplevel(window);dialog.title('Run Project Command');dialog.transient(window)
        body=ttk.Frame(dialog,padding=14);body.pack(fill='both',expand=True);body.columnconfigure(1,weight=1)
        ttk.Label(body,text='Working directory').grid(row=0,column=0,sticky='w',padx=6,pady=6)
        ttk.Entry(body,textvariable=folder_var,width=44).grid(row=0,column=1,sticky='ew')
        def browse():
            directory=filedialog.askdirectory(parent=dialog)
            if directory:load_project(directory)
        ttk.Button(body,text='Browse…',command=browse).grid(row=0,column=2,padx=5)
        ttk.Button(body,text='Current Project',command=lambda:load_project(project_directory())).grid(row=1,column=1,sticky='w',pady=5)
        ttk.Label(body,text='Command').grid(row=2,column=0,sticky='w',padx=6,pady=6)
        entry=ttk.Entry(body,textvariable=command_var,width=44);entry.grid(row=2,column=1,columnspan=2,sticky='ew')
        def execute():run();dialog.destroy()
        entry.bind('<Return>',lambda event:execute());entry.focus_set()
        actions=ttk.Frame(body);actions.grid(row=3,column=0,columnspan=3,sticky='ew',pady=10)
        ttk.Button(actions,text='Run',command=execute).pack(side='right')
        ttk.Button(actions,text='Save Project Command',command=save_config).pack(side='left')
        ttk.Button(actions,text='Cancel',command=dialog.destroy).pack(side='right',padx=5)

    def choose_directory():
        directory=filedialog.askdirectory(parent=window,initialdir=folder_var.get())
        if directory:load_project(directory);status.set('Directory selected. Restart Shell applies it to the shell.')

    def shell_settings():
        dialog=tk.Toplevel(window);dialog.title('Terminal Shell');dialog.transient(window)
        body=ttk.Frame(dialog,padding=14);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Shell executable (blank uses the system shell)').pack(anchor='w')
        value=tk.StringVar(dialog,shell_var.get());ttk.Entry(body,textvariable=value,width=54).pack(fill='x',pady=8)
        def browse():
            selected=filedialog.askopenfilename(parent=dialog,title='Choose shell executable')
            if selected:value.set(selected)
        ttk.Button(body,text='Browse…',command=browse).pack(anchor='w')
        def save_shell():
            shell_var.set(value.get().strip());configs['_shell']=shell_var.get()
            try:api._editor.write_data(config_path,json.dumps(configs,indent=2).encode('utf-8'))
            except OSError as error:messagebox.showerror('Shell settings',str(error),parent=dialog);return
            status.set('Shell saved. New / Restart Shell applies the change.');dialog.destroy()
        ttk.Button(body,text='Save',command=save_shell).pack(anchor='e',pady=10)

    def active_text():return interactive.text if view_mode.get()=='shell' else output

    def copy_selection():
        text=active_text();selection=text.tag_ranges('sel')
        if selection:root.clipboard_clear();root.clipboard_append(text.get(*selection))

    def clear_view():
        nonlocal decoder
        if view_mode.get()=='shell':interactive.clear()
        else:
            output.configure(state='normal');output.delete('1.0','end');output.configure(state='disabled');decoder=AnsiDecoder()

    def show(parent=None):
        nonlocal window,folder_var,command_var,output,status,interactive,view_mode,runner_frame
        if window is not None and window.winfo_exists():window.deiconify();window.lift();return window
        if parent is None:
            api._editor.plugin_sidebar.open(api.plugin_id);return
        window=create_window(root,parent);window.title('Project Terminal');window.protocol('WM_DELETE_WINDOW',close)
        folder_var=tk.StringVar(window);command_var=tk.StringVar(window);status=tk.StringVar(window,'Ready.')
        view_mode=tk.StringVar(window,'shell')
        menu_bar=ttk.Frame(window);menu_bar.pack(fill='x')
        terminal_menu=tk.Menu(menu_bar,tearoff=False);edit_menu=tk.Menu(menu_bar,tearoff=False)
        project_menu=tk.Menu(menu_bar,tearoff=False);settings_menu=tk.Menu(menu_bar,tearoff=False);view_menu=tk.Menu(menu_bar,tearoff=False)
        for label,menu in (('Terminal',terminal_menu),('Edit',edit_menu),('Project',project_menu),('View',view_menu),('Settings',settings_menu)):
            ttk.Menubutton(menu_bar,text=label,menu=menu).pack(side='left')
        terminal_menu.add_command(label='New / Restart Shell',command=start_shell)
        terminal_menu.add_command(label='Interrupt (Ctrl+C)',command=lambda:interactive.send('\x03'))
        terminal_menu.add_command(label='Stop Shell',command=lambda:interactive.stop())
        terminal_menu.add_separator();terminal_menu.add_command(label='Run Command…',command=command_settings)
        terminal_menu.add_command(label='Run Saved Command',command=run)
        terminal_menu.add_command(label='Stop Command',command=stop)
        terminal_menu.add_separator();terminal_menu.add_command(label='Install / Repair Engine',command=lambda:interactive.install())
        terminal_menu.add_command(label='Close Terminal',command=close)
        edit_menu.add_command(label='Copy',command=copy_selection,accelerator='Ctrl+Shift+C')
        edit_menu.add_command(label='Paste',command=lambda:interactive.paste() if view_mode.get()=='shell' else None,accelerator='Ctrl+Shift+V')
        edit_menu.add_command(label='Select All',command=lambda:active_text().tag_add('sel','1.0','end-1c'))
        edit_menu.add_command(label='Clear',command=clear_view)
        project_menu.add_command(label='Choose Working Directory…',command=choose_directory)
        project_menu.add_command(label='Use Current Project',command=lambda:load_project(project_directory()))
        project_menu.add_command(label='Command Settings…',command=command_settings)
        project_menu.add_command(label='Save Project Command',command=save_config)
        for label,value in (('Interactive Shell','shell'),('Command Output','runner')):
            view_menu.add_radiobutton(label=label,value=value,variable=view_mode,command=display_mode)
        settings_menu.add_command(label='Appearance…',command=show_settings)
        settings_menu.add_command(label='Shell…',command=shell_settings)
        content=ttk.Frame(window);content.pack(fill='both',expand=True)
        interactive=InteractiveTerminal(content,root,status,appearance,shell_var.get)
        runner_frame=ttk.Frame(content)
        output=tk.Text(runner_frame,wrap='none',font=terminal_font,background=appearance['background'],foreground=appearance['foreground'],borderwidth=0,padx=10,pady=8,state='disabled')
        output._terminal_role='runner';output.grid(row=0,column=0,sticky='nsew');runner_frame.columnconfigure(0,weight=1);runner_frame.rowconfigure(0,weight=1)
        scroll=ttk.Scrollbar(runner_frame,command=output.yview);scroll.grid(row=0,column=1,sticky='ns');output.configure(yscrollcommand=scroll.set)
        horizontal=ttk.Scrollbar(runner_frame,orient='horizontal',command=output.xview);horizontal.grid(row=1,column=0,sticky='ew');output.configure(xscrollcommand=horizontal.set)
        ttk.Label(window,textvariable=status,wraplength=520).pack(fill='x',padx=8,pady=4)
        apply_appearance();load_project(project_directory());display_mode();poll()
        root.after_idle(lambda:interactive.start(folder_var.get()) if not shutting_down.is_set() and window.winfo_exists() else None)
        return window

    def show_settings():
        nonlocal settings_window, appearance
        if api._editor.closing:return
        if window is None or not window.winfo_exists():show();root.after(200,show_settings);return
        if settings_window is not None and settings_window.winfo_exists():
            settings_window.lift(); return
        settings_window = dialog = tk.Toplevel(window)
        dialog.title("Terminal Settings"); dialog.geometry("740x740"); dialog.minsize(680, 680)
        dialog.transient(window)
        body = ttk.Frame(dialog, padding=16); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Terminal appearance", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        current_preset = next((name for name, values in PRESETS.items()
                               if all(appearance[key] == value for key, value in values.items())
                               and appearance['palette'] == values.get('palette', PALETTE)), 'Custom')
        selected = tk.StringVar(dialog, current_preset)
        preset = ttk.Combobox(body, textvariable=selected, values=('Custom', *PRESETS), state="readonly")
        preset.pack(fill="x", pady=(0, 8))
        preview = tk.Text(body, height=4, wrap="none", font=terminal_font, borderwidth=0, padx=10, pady=8, state="disabled")
        preview.pack(fill="x", pady=(0, 10))
        notebook = ttk.Notebook(body); notebook.pack(fill="both", expand=True)
        general = ttk.Frame(notebook, padding=12); palette = ttk.Frame(notebook, padding=12)
        notebook.add(general, text="General"); notebook.add(palette, text="ANSI palette")
        categories = ttk.Frame(notebook, padding=12); notebook.add(categories, text="Categories")
        variables = {key: tk.StringVar(dialog, str(value)) for key, value in appearance.items() if key not in ("palette", "ansi", "force_color", "categorize")}
        ansi = tk.BooleanVar(dialog, appearance["ansi"]); force = tk.BooleanVar(dialog, appearance["force_color"])
        categorize = tk.BooleanVar(dialog, appearance["categorize"])
        palette_vars = [tk.StringVar(dialog, value) for value in appearance["palette"]]
        def color_row(parent, label, variable, row):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(parent, textvariable=variable, width=12).grid(row=row, column=1, sticky="ew", padx=8)
            def choose():
                value = colorchooser.askcolor(variable.get(), parent=dialog)[1]
                if value: variable.set(value)
            ttk.Button(parent, text="Choose…", command=choose).grid(row=row, column=2)
        for row, (key, label) in enumerate((("command", "User commands / user messages"), ("status", "System / info messages"),
                                           ("warning", "Warnings"), ("error", "Errors"), ("success", "Success / completed"))):
            color_row(categories, label, variables[key], row)
        ttk.Checkbutton(categories, text="Automatically color recognized plain-text categories", variable=categorize).grid(row=5, column=0, columnspan=3, sticky="w", pady=12)
        ttk.Label(categories, text="Recognizes prefixes such as USER:, SYSTEM:, WARNING:, ERROR:, SUCCESS: and common build diagnostics.\nProgram-supplied ANSI colors take precedence. Unrecognized output uses the normal text color.",
                  wraplength=600, style="Muted.TLabel").grid(row=6, column=0, columnspan=3, sticky="w")
        for row, key in enumerate(("background", "foreground", "command", "status", "error")):
            color_row(general, key.title(), variables[key], row)
        for row, key in enumerate(("font_family", "font_size", "encoding"), 5):
            ttk.Label(general, text=key.replace("_", " ").title()).grid(row=row, column=0, sticky="w", pady=4)
            if key == "encoding":
                widget = ttk.Combobox(general, textvariable=variables[key], values=("utf-8", "locale", "cp1252", "cp850", "cp437"))
            else: widget = ttk.Entry(general, textvariable=variables[key])
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Checkbutton(general, text="Interpret ANSI colors", variable=ansi).grid(row=8, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Checkbutton(general, text="Request colored output from programs (force color)", variable=force).grid(row=9, column=0, columnspan=3, sticky="w")
        for index, variable in enumerate(palette_vars):
            group = ttk.Frame(palette); group.grid(row=index % 8, column=index // 8, padx=8, pady=3)
            color_row(group, str(index), variable, 0)
        def update_preview(*args):
            candidate = dict(appearance)
            candidate.update({key: variables[key].get() for key in ('background', 'foreground', 'command', 'status', 'error', 'warning', 'success')})
            candidate['palette'] = [variable.get() for variable in palette_vars]
            try: candidate = validate_settings(candidate)
            except ValueError: return
            preview.configure(state="normal", background=candidate['background'], foreground=candidate['foreground'])
            preview.delete('1.0', 'end')
            preview.tag_configure('command', foreground=candidate['command'])
            preview.tag_configure('error', foreground=candidate['error'])
            preview.tag_configure('warning', foreground=candidate['warning'])
            preview.tag_configure('success', foreground=candidate['success'])
            preview.insert('end', '$ python app.py\n', ('command',))
            preview.insert('end', 'Build complete  ', ('success',))
            preview.insert('end', 'Warning  ', ('warning',))
            preview.insert('end', 'Example error\n', ('error',))
            for index, color in enumerate(candidate['palette']):
                tag = 'color_' + str(index)
                preview.tag_configure(tag, foreground=color)
                preview.insert('end', f'{index:02} ', (tag,))
            preview.configure(state="disabled")
        def defaults(*args):
            chosen = PRESETS.get(selected.get())
            if chosen is None: return
            for key, value in chosen.items():
                if key != 'palette': variables[key].set(value)
            for variable, color in zip(palette_vars, chosen.get('palette', PALETTE)): variable.set(color)
        for key in ('background', 'foreground', 'command', 'status', 'error', 'warning', 'success'):
            variables[key].trace_add('write', update_preview)
        for variable in palette_vars: variable.trace_add('write', update_preview)
        update_preview()
        preset.bind("<<ComboboxSelected>>", defaults)
        notice = ttk.Label(body, text="Defaults are shared across Windows, macOS, and Linux. Blank font uses the system monospace font.\nEncoding and force-color changes affect the next command.", wraplength=650, style="Muted.TLabel")
        notice.pack(fill="x", pady=10)
        actions = ttk.Frame(body); actions.pack(fill="x")
        def save():
            nonlocal appearance
            try:
                candidate = {key: variable.get() for key, variable in variables.items()}
                candidate.update(font_size=int(candidate["font_size"]), ansi=ansi.get(), force_color=force.get(), categorize=categorize.get(), palette=[variable.get() for variable in palette_vars])
                candidate = validate_settings(candidate)
                updated = dict(configs, _appearance=candidate)
                api._editor.write_data(config_path, json.dumps(updated, indent=2).encode("utf-8"))
            except (ValueError, OSError) as error:
                messagebox.showerror("Terminal settings", str(error), parent=dialog); return
            configs.update(updated); appearance = candidate
            apply_appearance(); status.set("Terminal settings saved.")
            if hasattr(api, "emit"): api.emit("terminal_settings_changed")
        def reset():
            for key, variable in variables.items(): variable.set(str(DEFAULTS[key]))
            ansi.set(True); force.set(False); categorize.set(True); selected.set("Modern Graphite"); defaults()
        ttk.Button(actions, text="Restore defaults", command=reset).pack(side="left")
        ttk.Button(actions, text="Save & apply", command=save, style="Accent.TButton").pack(side="right")
        ttk.Button(actions, text="Close", command=dialog.destroy).pack(side="right", padx=8)

    api.add_panel(lambda parent: show(parent), width=600)
    api.add_command("Project Terminal", show)
    api.add_command("Project Terminal Settings…", show_settings)
    if hasattr(api, "provide_service"):
        api.provide_service("show", show)
        api.provide_service("get_settings", lambda: validate_settings(appearance))
        api.provide_service('run_command',run_command)
        api.provide_service('stop_command',stop)
        api.provide_service('command_settings',command_settings)
        api.provide_service('appearance',show_settings)
        api.provide_service('send_input',lambda value:interactive.send(value) if interactive else False)
        api.provide_service('start_shell',start_shell)
        api.provide_service('stop_shell',lambda:interactive.stop() if interactive else None)
