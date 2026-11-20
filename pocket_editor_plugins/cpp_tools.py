"""C/C++ formatting, clangd autocomplete, syntax squiggles and GDB debugging."""
import copy
import json
from pathlib import Path
import queue
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from pocket_editor_plugins._cpp_language import (
    STYLES, MAX_SOURCE, ToolWorker, is_cpp, read_settings, project_root, executable)
from pocket_editor_plugins._cpp_debug import Debugger
from pocket_editor_plugins._php_completion import prepare_completion
from pocket_editor_plugins._docking import create_window
from pocket_editor_plugins import _loading


class CppTools:
    def __init__(self, api):
        self.api, self.editor = api, api._editor
        self.root, self.text = self.editor.root, self.editor.buffer
        self.settings_path = Path(self.editor.settings_path).with_name('cpp_tools_settings.json')
        self.settings = read_settings(self.settings_path)
        self.worker = ToolWorker()
        self.closed = False
        self.window = self.settings_window = self.popup = None
        self.requested_tab = 0
        self.listing = self.issues = self.variables = self.stack = self.output = None
        self.pending_complete = self.pending_syntax = None
        self.ticket = 0;self.jobs = {};self.latest_complete = None
        self.shown = [];self.completion_capture = None
        self.diagnostics = [];self.diagnostic_capture = None;self.squiggles = [];self.paint_signature = None
        self.auto_error = self.syntax_error = False
        self.debugger = None;self.debug_running = False;self.debug_stopped = False
        self.debug_output = '';self.debug_variables = [];self.debug_stack = []
        self.debug_loading = None;self.debug_pending = set()
        self.breakpoints = set();self.debug_buttons = {}
        self.program = tk.StringVar(self.root)
        self.debug_arguments = tk.StringVar(self.root)
        self.watch = tk.StringVar(self.root)
        self.status = tk.StringVar(self.root, 'Ready. Open a C/C++ file; use Settings to detect tools.')
        self.bindings = []
        for sequence, callback in (
            ('<Control-space>', self.manual), ('<KeyRelease>', self.key_released),
            ('<Return>', self.accept), ('<Tab>', self.accept), ('<Escape>', self.escape),
            ('<Up>', lambda event:self.navigate(-1)), ('<Down>', lambda event:self.navigate(1)),
            ('<FocusOut>', lambda event:self.dismiss()), ('<ButtonRelease-1>', lambda event:self.dismiss()),
            ('<Configure>', lambda event:self.paint_squiggles())):
            self.bindings.append((sequence,self.text.bind(sequence,callback,add='+')))
        api.on('document_changed', self.changed)
        api.on('document_saved', self.changed)
        api.on('project_changed', self.changed)
        api.on_cleanup(self.cleanup)
        api.add_panel(self.mount, width=420)
        api.add_command('C/C++ Tools', self.show)
        api.add_command('Format C/C++', self.format_document)
        api.add_command('C/C++ Autocomplete', self.manual)
        api.add_command('Check C/C++ Syntax', self.check_syntax)
        api.add_command('C/C++ Debugger', self.show_debugger)
        api.add_command('Toggle C/C++ Breakpoint', self.toggle_breakpoint)
        api.add_command('C/C++ Settings', self.preferences)
        api.add_command('C/C++ Help', self.help)
        api.provide_service('format', self.format_document)
        api.provide_service('complete', self.manual)
        api.provide_service('check_syntax', self.check_syntax)
        self.poll_job = self.root.after(80,self.poll)

    def capture(self):
        source = self.api.get_text()
        offset = len(self.text.get('1.0','insert'))
        return (self.api.get_path(),source,offset,self.editor.document_generation,
                self.editor.document_revision,self.text.index('insert'))

    def matches(self, capture, cursor=False):
        if self.api.get_path() != capture[0] or self.editor.document_generation != capture[3]:return False
        if self.editor.document_revision != capture[4] or self.api.get_text() != capture[1]:return False
        return not cursor or self.text.index('insert') == capture[5]

    def directory(self, path):
        try:selected = self.api.call_service('project_explorer.get_project')
        except LookupError:selected = None
        return project_root(path,selected)

    def request(self, kind, manual=True):
        if self.closed:return
        path = self.api.get_path()
        if not is_cpp(path):
            if manual:self.status.set('Open or save a C/C++ source/header file first.');self.show()
            return
        capture = self.capture()
        if len(capture[1].encode('utf-8')) > MAX_SOURCE:
            self.status.set('Language tools support documents up to 4 MiB.');return
        if kind in ('format','build') and any(job[0] == kind for job in self.jobs.values()):return
        if kind == 'build':
            if path.suffix.lower() not in ('.c','.cc','.cpp','.cxx','.c++'):
                self.status.set('Build a C/C++ source file, or browse an existing debug executable.');return
            try:saved = path.read_text(encoding='utf-8-sig')
            except (OSError,UnicodeError) as error:self.status.set(str(error));return
            if saved != capture[1]:
                self.status.set('Save this file before building. The build uses the saved source.');return
        if len(capture[1].encode('utf-8')) > MAX_SOURCE:
            self.status.set('C/C++ tools support documents up to 4 MiB.');return
        if kind == 'complete':
            for old,job in list(self.jobs.items()):
                if job[0] == 'complete':_loading.cancel(self.api,job[3]);self.jobs.pop(old)
        self.ticket += 1;ticket = self.ticket
        try:self.worker.submit(ticket,kind,capture,self.settings,self.directory(path))
        except queue.Full:self.status.set('C/C++ tools are busy. Try again shortly.');return
        token = _loading.begin(self.api,{'format':'Formatting C/C++…','complete':'Loading C/C++ suggestions…',
            'syntax':'Checking C/C++ syntax…','build':'Building debug executable…'}[kind])
        self.jobs[ticket] = (kind,capture,manual,token)
        if kind == 'complete':self.latest_complete = ticket
        if manual:self.status.set({'format':'Formatting…','complete':'Loading suggestions…',
            'syntax':'Checking syntax…','build':'Building with debug symbols…'}[kind])

    def format_document(self):self.request('format')
    def check_syntax(self):self.syntax_error = False;self.request('syntax')
    def manual(self,event=None):
        if not is_cpp(self.api.get_path()):return None
        self.auto_error = False;self.request('complete');return 'break'

    def changed(self, **event):
        self.dismiss()
        if self.diagnostic_capture is not None and not self.matches(self.diagnostic_capture):self.clear_diagnostics()
        self.schedule_syntax()

    def cancel_timer(self, name):
        job = getattr(self,name)
        if job is not None:self.root.after_cancel(job);setattr(self,name,None)

    def schedule_syntax(self):
        self.cancel_timer('pending_syntax')
        if self.settings['syntax_automatic'] and not self.syntax_error and is_cpp(self.api.get_path()):
            def check():self.pending_syntax = None;self.request('syntax',False)
            self.pending_syntax = self.root.after(900,check)

    def key_released(self,event):
        if not is_cpp(self.api.get_path()):return
        if self.diagnostic_capture is not None and not self.matches(self.diagnostic_capture):self.clear_diagnostics()
        if event.keysym in ('Up','Down','Left','Right','Return','Tab','Escape','Control_L','Control_R','Shift_L','Shift_R'):return
        self.dismiss();self.schedule_syntax();self.cancel_timer('pending_complete')
        if self.settings['automatic'] and not self.auto_error:
            prefix = self.text.get('insert linestart','insert')
            if re.search(r'(?:[A-Za-z_]\w|\.|->|::)$',prefix):
                def complete():self.pending_complete = None;self.request('complete',False)
                self.pending_complete = self.root.after(self.settings['delay_ms'],complete)

    def dismiss(self):
        if self.popup is not None and self.popup.winfo_exists():self.popup.withdraw()
        self.shown = [];self.completion_capture = None

    def escape(self,event=None):
        if self.popup is not None and self.popup.winfo_viewable():self.dismiss();return 'break'

    def navigate(self,direction):
        if self.popup is None or not self.popup.winfo_viewable():return None
        selected = self.listing.curselection();index = min(len(self.shown)-1,max(0,(selected[0] if selected else 0)+direction))
        self.listing.selection_clear(0,'end');self.listing.selection_set(index);self.listing.see(index);return 'break'

    def show_suggestions(self, items, capture):
        self.dismiss()
        if not isinstance(items,list):return
        items = [item for item in items if isinstance(item,dict) and isinstance(item.get('label'),str)][:150]
        if not items:self.status.set('No C/C++ suggestions at this position. Check project includes and compilation database.');return
        if self.popup is None or not self.popup.winfo_exists():
            self.popup = tk.Toplevel(self.root);self.popup.overrideredirect(True);self.popup.transient(self.root)
            body = ttk.Frame(self.popup,padding=4);body.pack(fill='both',expand=True)
            self.listing = tk.Listbox(body,height=8,width=48,background='#11161c',foreground='#d1dae4',
                selectbackground='#1597f5',selectforeground='white',exportselection=False,takefocus=False,
                font=self.editor.ui_font,highlightthickness=1,highlightbackground='#27323d')
            self.listing.pack(side='left',fill='both',expand=True)
            scroll = ttk.Scrollbar(body,command=self.listing.yview);scroll.pack(side='right',fill='y')
            self.listing.configure(yscrollcommand=scroll.set)
            def click(event):
                index = self.listing.nearest(event.y)
                self.listing.selection_clear(0,'end');self.listing.selection_set(index);self.accept();return 'break'
            self.listing.bind('<ButtonRelease-1>',click)
        self.shown = items;self.completion_capture = capture
        self.listing.delete(0,'end')
        for item in items:self.listing.insert('end',item['label'])
        self.listing.selection_set(0)
        bounds = self.text.bbox('insert')
        if not bounds:self.dismiss();return
        self.popup.update_idletasks()
        x = min(self.text.winfo_rootx()+bounds[0],max(0,self.root.winfo_screenwidth()-self.popup.winfo_reqwidth()))
        y = self.text.winfo_rooty()+bounds[1]+bounds[3]
        if y+self.popup.winfo_reqheight()>self.root.winfo_screenheight():y -= self.popup.winfo_reqheight()+bounds[3]
        self.popup.geometry(f'+{max(0,x)}+{max(0,y)}');self.popup.deiconify();self.popup.lift()
        self.status.set(f'{len(items)} suggestions · Enter/Tab to insert · Escape to close')

    def accept(self,event=None):
        if self.popup is None or not self.popup.winfo_viewable() or not self.shown:return None
        capture = self.completion_capture
        if not self.matches(capture,True):self.dismiss();return 'break'
        selected = self.listing.curselection();item = self.shown[selected[0] if selected else 0]
        try:edits,caret,stops = prepare_completion(capture[1],capture[2],item)
        except (ValueError,KeyError,TypeError) as error:self.status.set(str(error));return 'break'
        self.dismiss();self.text.edit_separator()
        for start,end,value,primary in reversed(edits):self.text.replace(f'1.0 +{start}c',f'1.0 +{end}c',value)
        self.text.edit_separator();self.text.mark_set('insert',f'1.0 +{caret}c')
        if stops and stops[0][2]>stops[0][1]:self.text.tag_add('sel',f'1.0 +{stops[0][1]}c',f'1.0 +{stops[0][2]}c')
        self.editor.refresh();self.schedule_syntax();return 'break'

    def clear_diagnostics(self):
        self.paint_signature = None
        for canvas in self.squiggles:
            if canvas.winfo_exists():canvas.destroy()
        self.squiggles = [];self.diagnostics = [];self.diagnostic_capture = None
        if self.issues is not None and self.issues.winfo_exists():self.issues.delete(*self.issues.get_children())

    def show_diagnostics(self, items, capture):
        self.clear_diagnostics();self.diagnostics = items;self.diagnostic_capture = capture
        if self.issues is not None and self.issues.winfo_exists():
            for index,item in enumerate(items):self.issues.insert('', 'end', iid=str(index), values=(item['severity'],item['line'],item['message']))
        errors = sum(item['severity'] in ('error','fatal error') for item in items)
        self.status.set(f'Syntax: {errors} errors, {len(items)-errors} warnings. Double-click an issue to jump.')
        self.paint_squiggles()

    def paint_squiggles(self):
        signature = (str(self.text.widget),self.text.yview(),self.text.xview(),self.text.winfo_width(),self.text.winfo_height(),
                     self.editor.document_revision,str(self.text.cget('font')),self.text.cget('background'),id(self.diagnostics))
        if signature == self.paint_signature:return
        self.paint_signature = signature
        for canvas in self.squiggles:
            if canvas.winfo_exists():canvas.destroy()
        self.squiggles = []
        if not self.diagnostic_capture or not self.matches(self.diagnostic_capture):return
        source = self.diagnostic_capture[1];lines = source.split('\n')
        for item in self.diagnostics[:100]:
            if Path(item['path']).resolve() != self.api.get_path().resolve():continue
            line = item['line'];column = item['column']
            if not 1<=line<=len(lines):continue
            value = lines[line-1];column = min(max(0,column-1),len(value))
            match = re.match(r'\w+',value[column:]);length = len(match[0]) if match else 1
            # Split at wrapped display lines by grouping visible character boxes.
            groups = []
            for offset in range(column,min(column+length,max(column+1,len(value)))):
                box = self.text.bbox(f'{line}.{offset}')
                if not box:continue
                x,y,width,height = box
                if offset >= len(value):width = 6
                if groups and groups[-1][1]==y and x<=groups[-1][2]+1:groups[-1][2]=x+max(6,width)
                else:groups.append([x,y,x+max(6,width),height])
            for x,y,end,height in groups:
                width = max(6,end-x)
                canvas = tk.Canvas(self.text.widget,width=width,height=4,highlightthickness=0,
                    background=self.text.cget('background'),takefocus=False)
                points = [number for step in range(0,width+2,2) for number in (step,1 if (step//2)%2==0 else 3)]
                canvas.create_line(*points,fill='#ff5252' if item['severity'] in ('error','fatal error') else '#e5b454',width=1)
                canvas.place(x=x,y=y+height-4,width=width,height=4)
                canvas.bind('<Button-1>',lambda event,item=item:self.goto_issue(item))
                self.squiggles.append(canvas)

    def goto_issue(self,item=None):
        if item is None:
            if self.diagnostic_capture is None or not self.matches(self.diagnostic_capture):return
            selected = self.issues.selection()
            if not selected:return
            item = self.diagnostics[int(selected[0])]
        path = Path(item['path'])
        if path != self.api.get_path() and path.is_file():self.editor.open_path(path)
        if path == self.api.get_path():
            self.text.mark_set('insert',f"{item['line']}.{max(0,item['column']-1)}");self.text.see('insert');self.text.focus_set()
        self.status.set(item['message'])

    def mount(self,parent):self.show(parent);return self.window

    def show(self,parent=None):
        if self.closed:return
        if parent is None:
            self.editor.plugin_sidebar.open('cpp_tools')
            return
        if self.window is not None and self.window.winfo_exists():
            if self.window.master is parent and getattr(self.window,'_docked_panel',False):
                self.window.deiconify();self.window.lift();return
            # Migrate an older detached surface without restarting tools or losing inputs.
            self.window.destroy()
        self.window = create_window(self.root,parent)
        self.window.title('C/C++ Tools');self.window.geometry('780x620');self.window.minsize(360,420)
        if parent is None:self.window.protocol('WM_DELETE_WINDOW',self.window.withdraw)
        body = ttk.Frame(self.window,padding=10);body.pack(fill='both',expand=True)
        top = ttk.Frame(body);top.pack(fill='x')
        for index,(label,callback) in enumerate((('Format',self.format_document),('Complete',self.manual),('Check',self.check_syntax),('Settings',self.preferences))):
            ttk.Button(top,text=label,command=callback).grid(row=index//2,column=index%2,sticky='ew',padx=2,pady=2)
        top.columnconfigure(0,weight=1);top.columnconfigure(1,weight=1)
        self.notebook = ttk.Notebook(body);self.notebook.pack(fill='both',expand=True,pady=8)
        issue_tab = ttk.Frame(self.notebook);debug_tab = ttk.Frame(self.notebook,padding=6)
        self.notebook.add(issue_tab,text='Errors & warnings');self.notebook.add(debug_tab,text='Debugger')
        self.issues = ttk.Treeview(issue_tab,columns=('severity','line','message'),show='headings',height=8)
        for column,title,width in (('severity','Type',70),('line','Line',45),('message','Message',250)):
            self.issues.heading(column,text=title);self.issues.column(column,width=width,stretch=column=='message')
        scroll = ttk.Scrollbar(issue_tab,command=self.issues.yview);scroll.pack(side='right',fill='y')
        self.issues.configure(yscrollcommand=scroll.set);self.issues.pack(fill='both',expand=True)
        self.issues.bind('<Double-1>',lambda event:self.goto_issue())
        row = ttk.Frame(debug_tab);row.pack(fill='x')
        ttk.Label(row,text='Program').pack(side='left')
        ttk.Entry(row,textvariable=self.program,width=20).pack(side='left',fill='x',expand=True,padx=5)
        ttk.Button(row,text='Browse…',command=self.choose_program).pack(side='left')
        ttk.Label(debug_tab,text='Arguments (separate with |; no shell)').pack(anchor='w',pady=(6,0))
        ttk.Entry(debug_tab,textvariable=self.debug_arguments).pack(fill='x')
        controls = ttk.Frame(debug_tab);controls.pack(fill='x',pady=6)
        actions = [('Build file',self.build),('Start',self.start_debug),('Continue',lambda:self.debug_command('continue')),
                   ('Pause',lambda:self.debug_command('pause')),('Step over',lambda:self.debug_command('next')),
                   ('Step into',lambda:self.debug_command('step')),('Step out',lambda:self.debug_command('finish')),('Stop',self.stop_debug),
                   ('Breakpoint',self.toggle_breakpoint)]
        self.debug_buttons = {}
        for index,(label,callback) in enumerate(actions):
            button = ttk.Button(controls,text=label,command=callback);button.grid(row=index//3,column=index%3,sticky='ew',padx=2,pady=2)
            controls.columnconfigure(index%3,weight=1);self.debug_buttons[label]=button
        tabs = ttk.Notebook(debug_tab);tabs.pack(fill='both',expand=True)
        self.output = tk.Text(tabs,height=6,wrap='word',background='#11161c',foreground='#d1dae4',font=self.editor.editor_font,state='disabled')
        self.variables = ttk.Treeview(tabs,columns=('name','value'),show='headings',height=5)
        self.variables.heading('name',text='Variable');self.variables.heading('value',text='Value')
        self.stack = ttk.Treeview(tabs,columns=('function','location'),show='headings',height=5)
        self.stack.heading('function',text='Function');self.stack.heading('location',text='Location')
        for widget,title in ((self.output,'Output'),(self.variables,'Variables'),(self.stack,'Call stack')):tabs.add(widget,text=title)
        self.stack.bind('<Double-1>',self.select_frame)
        row = ttk.Frame(debug_tab);row.pack(fill='x',pady=(6,0))
        ttk.Entry(row,textvariable=self.watch,width=18).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Inspect',command=lambda:self.debug_command('watch',self.watch.get())).pack(side='right')
        ttk.Label(body,textvariable=self.status,wraplength=360,style='Muted.TLabel').pack(fill='x')
        if self.diagnostic_capture:self.show_diagnostics(list(self.diagnostics),self.diagnostic_capture)
        self.notebook.select(self.requested_tab)
        self.render_debug_data()
        self.update_debug_controls()

    def show_debugger(self):
        self.requested_tab = 1
        self.show()
        if self.window is not None and self.window.winfo_exists():self.notebook.select(1)

    def choose_program(self):
        value = filedialog.askopenfilename(parent=self.window,title='Choose executable built with debug symbols')
        if value:self.program.set(value)

    def build(self):
        if self.debugger:self.status.set('Stop the debugger before rebuilding.');return
        self.request('build')

    def start_debug(self):
        if self.debugger:self.status.set('A debug session is already active.');return
        program = Path(self.program.get()).expanduser()
        if not program.is_file():self.status.set('Choose an executable, or use Build file first.');return
        self.show_debugger()
        self.debug_pending={'startup'}
        self.debug_loading = _loading.begin(self.api,'Starting C/C++ debugger…')
        self.debugger = Debugger(program,[arg for arg in self.debug_arguments.get().split('|') if arg],program.parent,self.settings,sorted(self.breakpoints))
        self.debug_running = False;self.debug_stopped = False;self.status.set('Starting debugger…');self.update_debug_controls()

    def stop_debug(self):
        _loading.cancel(self.api,self.debug_loading);self.debug_loading=None;self.debug_pending.clear()
        if self.debugger:self.debugger.close();self.status.set('Stopping debugger…')

    def debug_command(self,kind,value=None):
        if not self.debugger:return
        if kind in ('next','step','finish','continue','watch','frame') and not self.debug_stopped:
            self.status.set('Pause at a breakpoint before inspecting or stepping.');return
        try:self.debugger.submit(kind,value)
        except queue.Full:self.status.set('Debugger is busy; please wait.')

    def toggle_breakpoint(self):
        path = self.api.get_path()
        if not is_cpp(path):self.status.set('Open a C/C++ file to set a breakpoint.');return
        location = str(path.resolve())+':'+self.text.index('insert').split('.')[0]
        if location in self.breakpoints:self.breakpoints.remove(location);action='Removed'
        else:self.breakpoints.add(location);action='Set'
        if self.debugger:
            try:self.debugger.submit('breakpoint',location)
            except queue.Full:self.status.set('Debugger is busy; retry the breakpoint.');return
        self.status.set(action+' breakpoint at '+location)

    def select_frame(self,event=None):
        selected = self.stack.selection()
        if selected:self.debug_command('frame',int(selected[0]))

    def update_debug_controls(self):
        for label,button in self.debug_buttons.items():
            if not button.winfo_exists():continue
            allowed = (self.debugger is None if label in ('Build file','Start') else
                       self.debugger is not None if label=='Stop' else self.debug_running if label=='Pause' else
                       True if label=='Breakpoint' else self.debug_stopped)
            button.configure(state='normal' if allowed else 'disabled')

    def render_debug_data(self):
        if self.output is not None and self.output.winfo_exists():
            self.output.configure(state='normal');self.output.delete('1.0','end')
            self.output.insert('end',self.debug_output);self.output.see('end');self.output.configure(state='disabled')
        if self.variables is not None and self.variables.winfo_exists():
            self.variables.delete(*self.variables.get_children())
            for item in self.debug_variables:
                self.variables.insert('', 'end',values=(item.get('name',''),item.get('value','<expand with Inspect>')))
        if self.stack is not None and self.stack.winfo_exists():
            self.stack.delete(*self.stack.get_children())
            for item in self.debug_stack:
                frame=item.get('frame',item)
                self.stack.insert('', 'end',iid=str(frame.get('level',0)),values=(frame.get('func',''),
                    frame.get('fullname',frame.get('file',''))+':'+frame.get('line','')))

    def debug_events(self):
        debugger = self.debugger
        if not debugger:return
        received=False
        for _ in range(60):
            try:kind,value = debugger.events.get_nowait()
            except queue.Empty:break
            received=True
            if kind == 'output':self.debug_output=(self.debug_output+value)[-131072:]
            elif kind == 'error':
                self.status.set('Debugger: '+value);self.debug_pending.clear()
            elif kind == 'running':
                self.debug_running=True;self.debug_stopped=False;self.debug_pending.discard('startup');self.status.set('Program running.')
            elif kind == 'stopped':
                self.debug_running=False;self.debug_stopped=not value.get('reason','').startswith('exited')
                if self.debug_stopped:
                    _loading.cancel(self.api,self.debug_loading)
                    self.debug_loading=_loading.begin(self.api,'Loading debugger variables and stack…')
                    self.debug_pending={'variables','stack'}
                frame=value.get('frame',{});self.status.set('Stopped: '+value.get('reason','')+' '+frame.get('func',''))
                filename=frame.get('fullname');line=frame.get('line')
                if filename and line and Path(filename).is_file():
                    self.editor.open_path(Path(filename));self.text.mark_set('insert',f'{int(line)}.0');self.text.see('insert')
            elif kind == 'variables':
                self.debug_variables=value;self.debug_pending.discard('variables')
            elif kind == 'stack':
                self.debug_stack=value;self.debug_pending.discard('stack')
            elif kind == 'watch':self.status.set(value[0]+' = '+value[1])
            elif kind == 'closed':
                self.debugger=None;self.debug_running=self.debug_stopped=False;self.debug_pending.clear()
        if received:self.render_debug_data()
        self.update_debug_controls()
        if self.debug_loading is not None and not self.debug_pending:
            token=self.debug_loading;self.debug_loading=None
            _loading.end(self.api,token,ready=lambda:self.closed or not self.editor.plugin_sidebar.visible or
                         ('cpp_tools' not in self.editor.plugin_sidebar.pending and self.window is not None and self.window.winfo_exists()))

    def poll(self):
        if self.closed:return
        for _ in range(16):
            try:ticket,kind,capture,value,error = self.worker.results.get_nowait()
            except queue.Empty:break
            job = self.jobs.pop(ticket,None)
            if job is None:continue
            try:
                if kind=='complete' and ticket!=self.latest_complete:continue
                if error:
                    if kind=='complete':self.auto_error=True
                    if kind=='syntax':self.syntax_error=True
                    self.status.set('C/C++: '+error.splitlines()[0][:300])
                    if job[2]:messagebox.showerror('C/C++ tools',error[:8000],parent=self.root)
                    continue
                if kind == 'build':self.program.set(value);self.status.set('Debug build ready: '+value);continue
                if not self.matches(capture,kind=='complete'):continue
                if kind == 'format':
                    if value!=capture[1]:
                        cursor=self.text.index('insert');view=self.text.yview()[0]
                        self.api.replace_text(value);self.text.mark_set('insert',cursor);self.text.yview_moveto(view)
                        self.editor.refresh();self.api.emit('document_formatted',language='C/C++')
                    self.status.set('C/C++ formatted. Use Undo to revert; Save writes the file.');self.clear_diagnostics();self.schedule_syntax()
                elif kind=='complete':self.show_suggestions(value,capture)
                elif kind=='syntax':self.show_diagnostics(value,capture)
            finally:
                _loading.end(self.api,job[3])
        if self.diagnostic_capture is not None:
            if not self.matches(self.diagnostic_capture):self.clear_diagnostics()
            else:self.paint_squiggles()
        self.debug_events();self.poll_job=self.root.after(100,self.poll)

    def preferences(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():self.settings_window.lift();return
        window = self.settings_window = tk.Toplevel(self.root);window.title('C/C++ Settings');window.geometry('640x620');window.minsize(400,360)
        footer=ttk.Frame(window,padding=10);footer.pack(side='bottom',fill='x')
        viewport=ttk.Frame(window);viewport.pack(fill='both',expand=True)
        canvas=tk.Canvas(viewport,background='#11161c',highlightthickness=0)
        scrollbar=ttk.Scrollbar(viewport,command=canvas.yview);scrollbar.pack(side='right',fill='y');canvas.pack(fill='both',expand=True)
        canvas.configure(yscrollcommand=scrollbar.set);body=ttk.Frame(canvas,padding=12)
        item=canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
        values={key:tk.StringVar(window,str(self.settings[key])) for key in ('clang_format','clangd','compiler','gdb','style','cpp_standard','c_standard','delay_ms','compile_commands_dir')}
        for row,(key,label) in enumerate((('clang_format','clang-format executable'),('clangd','clangd executable'),('compiler','GCC/Clang compiler'),('gdb','GDB executable'),('compile_commands_dir','Compilation database directory (optional)'))):
            ttk.Label(body,text=label).grid(row=row*2,column=0,columnspan=2,sticky='w',pady=(8,2))
            ttk.Entry(body,textvariable=values[key]).grid(row=row*2+1,column=0,sticky='ew')
            def browse(key=key):
                path=filedialog.askdirectory(parent=window) if key=='compile_commands_dir' else filedialog.askopenfilename(parent=window)
                if path:values[key].set(path)
            ttk.Button(body,text='Browse…',command=browse).grid(row=row*2+1,column=1,padx=(6,0))
        row=10
        for key,label,options in (('style','Format style',STYLES),('cpp_standard','C++ standard',('c++11','c++14','c++17','c++20','c++23')),('c_standard','C standard',('c99','c11','c17','c23'))):
            ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',pady=5)
            ttk.Combobox(body,textvariable=values[key],values=options,state='readonly',width=12).grid(row=row,column=1,sticky='e');row+=1
        automatic=tk.BooleanVar(window,self.settings['automatic']);syntax=tk.BooleanVar(window,self.settings['syntax_automatic'])
        ttk.Checkbutton(body,text='Automatic suggestions',variable=automatic).grid(row=row,column=0,columnspan=2,sticky='w');row+=1
        ttk.Checkbutton(body,text='Check syntax after typing',variable=syntax).grid(row=row,column=0,columnspan=2,sticky='w');row+=1
        ttk.Label(body,text='Completion delay (100–1500 ms)').grid(row=row,column=0,sticky='w')
        ttk.Spinbox(body,textvariable=values['delay_ms'],from_=100,to=1500,width=8).grid(row=row,column=1,sticky='e');row+=1
        ttk.Label(body,text='Include directories (one per line; relative to project)').grid(row=row,column=0,columnspan=2,sticky='w',pady=(8,2));row+=1
        includes=tk.Text(body,height=4,width=35);includes.grid(row=row,column=0,columnspan=2,sticky='ew');includes.insert('1.0','\n'.join(self.settings['includes']))
        body.columnconfigure(0,weight=1)
        def wheel(event):
            canvas.yview_scroll(-3 if getattr(event,'num',None)==4 or getattr(event,'delta',0)>0 else 3,'units');return 'break'
        def bind_wheel(widget):
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):widget.bind(sequence,wheel,add='+')
            for child in widget.winfo_children():bind_wheel(child)
        bind_wheel(body);bind_wheel(canvas)
        def detect():
            report=[]
            for key,name in (('clang_format','clang-format'),('clangd','clangd'),('gdb','gdb')):
                try:path=executable(name,values[key].get());values[key].set(path);report.append(name+': found')
                except RuntimeError:report.append(name+': missing')
            self.status.set(' · '.join(report))
            messagebox.showinfo('C/C++ Tools','\n'.join(report)+'\n\nSee How to use for installation commands.',parent=window)
        def save():
            try:
                delay=int(values['delay_ms'].get())
                if not 100<=delay<=1500:raise ValueError
            except ValueError:messagebox.showerror('Settings','Delay must be 100–1500 ms.',parent=window);return
            selected={key:value.get().strip() for key,value in values.items()};selected.update(delay_ms=delay,automatic=automatic.get(),syntax_automatic=syntax.get(),includes=includes.get('1.0','end-1c').splitlines())
            try:self.editor.write_data(self.settings_path,json.dumps(selected,indent=2).encode())
            except OSError as error:messagebox.showerror('Settings',str(error),parent=window);return
            self.settings=read_settings(self.settings_path);self.auto_error=self.syntax_error=False
            self.dismiss();self.schedule_syntax();window.destroy();self.status.set('C/C++ settings saved.')
        ttk.Button(footer,text='Detect tools',command=detect).pack(side='left')
        ttk.Button(footer,text='How to use',command=self.help).pack(side='left',padx=6)
        ttk.Button(footer,text='Save Settings',command=save,style='Accent.TButton').pack(side='right')

    def help(self):
        messagebox.showinfo('C/C++ Tools',
            'Debian/Ubuntu: sudo apt install clang-format clangd g++ gdb\n'
            'Windows: install LLVM (clangd/clang-format) plus MinGW-w64 GCC/GDB; select executable paths in Settings.\n'
            'macOS: install LLVM and GCC/GDB separately. GDB may require code signing; LLDB is not supported.\n\n'
            'Format changes only the editor buffer. Undo and explicit Save are supported.\n'
            'Autocomplete: type two letters, . or -> or ::, or press Ctrl+Space. Enter/Tab accepts.\n'
            'Check shows syntax errors and red zigzag lines; warnings are amber.\n'
            'For projects, generate compile_commands.json (CMake: -DCMAKE_EXPORT_COMPILE_COMMANDS=ON).\n'
            'Syntax checks and single-file builds use the configured compiler, standard, and include paths.\n'
            'Build file compiles the saved current source to .pocket-debug. Multi-file projects: build externally, then Browse the executable.\n'
            'Set a breakpoint at the caret, Start, then Step/Continue. Variables and Call stack appear when paused.\n'
            'Debugger output is captured; interactive program input is not supported.',parent=self.root)

    def cleanup(self):
        if self.closed:return
        self.closed=True;self.worker.close()
        _loading.cancel(self.api,self.debug_loading);self.debug_loading=None
        if self.debugger:self.debugger.close()
        self.cancel_timer('pending_complete');self.cancel_timer('pending_syntax')
        if self.poll_job:self.root.after_cancel(self.poll_job);self.poll_job=None
        for _,_,_,token in self.jobs.values():_loading.cancel(self.api,token)
        self.jobs.clear();self.clear_diagnostics()
        for sequence,ident in self.bindings:self.text.unbind(sequence,ident)
        for window in (self.popup,self.settings_window,self.window):
            if window is not None and window.winfo_exists():window.destroy()


def register(api):CppTools(api)
