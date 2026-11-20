"""Persistent vertical plugin tabs and docked panels for Pocket Editor."""
import tkinter as tk
from tkinter import ttk

TITLES = {'project_explorer':'Project Explorer','project_terminal':'Terminal','git_project':'Git',
          'ai_assistant':'AI Assistant','codex_assistant':'Codex','team_project':'Team Project',
          'python_tools':'Python Tools','php_tools':'PHP Tools','cpp_tools':'C/C++ Tools'}
DEFAULT_PINS = ['project_explorer','search_replace','project_terminal','git_project','ai_assistant','codex_assistant','team_project']


class PluginSidebar:
    def __init__(self, editor, parent, settings):
        self.editor, self.root = editor, editor.root
        self.active = settings.get('sidebar_active', 'project_explorer')
        self.width = settings.get('sidebar_width', 320)
        self.pins = list(settings.get('sidebar_plugins', DEFAULT_PINS))
        self.hosts, self.buttons, self.visible = {}, {}, False
        self.tooltip = self.chooser = None
        self.pending = {}
        self.window_jobs = {}
        self.opened_windows = set()
        self.closed = False
        self.command_window = None
        self.rail = tk.Frame(parent, width=56, background='#0b1016', highlightbackground='#27323d', highlightthickness=1)
        self.rail.pack(side='left', fill='y'); self.rail.pack_propagate(False)
        foot=tk.Frame(self.rail,background='#0b1016');foot.pack(side='bottom',fill='x')
        tk.Button(foot,text='⚙',font=(editor.ui_font.cget('family'),18),background='#0b1016',foreground='#9aaab9',activebackground='#19212a',activeforeground='#ffffff',relief='flat',bd=0,highlightthickness=0,command=self.choose).pack(fill='x',ipady=12)
        self.canvas=tk.Canvas(self.rail,background='#0b1016',highlightthickness=0,width=54)
        self.canvas.pack(fill='both',expand=True)
        self.icons=tk.Frame(self.canvas,background='#0b1016')
        self.canvas.create_window((0,0),window=self.icons,anchor='nw',width=54)
        self.icons.bind('<Configure>',lambda event:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<MouseWheel>',self.scroll)
        self.panes=ttk.Panedwindow(parent,orient='horizontal');self.panes.pack(side='left',fill='both',expand=True)
        self.panel=ttk.Frame(self.panes,width=self.width)
        header=ttk.Frame(self.panel,padding=(14,9));header.pack(fill='x')
        self.title=ttk.Label(header,text='',style='Title.TLabel');self.title.pack(side='left',fill='x',expand=True)
        ttk.Button(header,text='×',width=2,command=self.hide).pack(side='right')
        self.body=ttk.Frame(self.panel);self.body.pack(fill='both',expand=True)
        self.editor_host=ttk.Frame(self.panes);self.panes.add(self.editor_host,weight=1)
        self.panes.bind('<ButtonRelease-1>',self.remember_width,add='+')
        self.panes.bind('<Configure>',lambda event:self.fit(),add='+')

    def scroll(self,event):
        self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units');return 'break'

    def caption(self,owner): return TITLES.get(owner,owner.replace('_',' ').title())

    def refresh(self):
        for button in self.buttons.values():button.destroy()
        self.buttons.clear()
        for owner in self.pins:
            if owner not in self.editor.plugin_apis:continue
            button=tk.Canvas(self.icons,width=54,height=52,background='#0b1016',highlightthickness=0,takefocus=True)
            button.pack(fill='x');self.buttons[owner]=button
            button.bind('<ButtonRelease-1>',lambda event,owner=owner:self.toggle(owner))
            button.bind('<ButtonRelease-3>',lambda event,owner=owner:self.show_commands(owner))
            button.bind('<Shift-F10>',lambda event,owner=owner:self.show_commands(owner))
            button.bind('<Return>',lambda event,owner=owner:self.toggle(owner))
            button.bind('<space>',lambda event,owner=owner:self.toggle(owner))
            button.bind('<Enter>',lambda event,owner=owner:self.show_tip(owner))
            button.bind('<Leave>',lambda event:self.hide_tip())
            button.bind('<FocusIn>',lambda event,owner=owner:self.show_tip(owner))
            button.bind('<FocusOut>',lambda event:self.hide_tip())
            button.bind('<MouseWheel>',self.scroll)
        if self.active in self.pins and self.active in self.editor.plugin_apis:self.open(self.active,save=False)
        elif self.visible:self.hide(save=False)
        self.draw()

    def draw(self):
        for owner,canvas in self.buttons.items():
            active=self.visible and self.active==owner
            canvas.configure(background='#141e29' if active else '#0b1016');canvas.delete('all')
            color='#299ef4' if active else '#8e9ba9'
            if active:canvas.create_rectangle(0,4,3,48,fill=color,outline='')
            if owner=='project_explorer':
                canvas.create_line(15,16,15,35,39,35,39,19,26,19,23,15,15,15,fill=color,width=2)
            elif owner=='search_replace':
                canvas.create_oval(15,12,33,30,outline=color,width=2);canvas.create_line(31,28,40,38,fill=color,width=2)
            elif owner=='project_terminal':
                canvas.create_rectangle(13,14,41,36,outline=color,width=2);canvas.create_text(27,25,text='>_',fill=color,font=self.editor.caption_font)
            else:
                canvas.create_rectangle(13,12,41,38,outline=color,width=1)
                text={'ai_assistant':'AI','codex_assistant':'CX','git_project':'Git','team_project':'TM','python_tools':'Py','php_tools':'PHP','cpp_tools':'C++'}.get(owner,owner[:2].upper())
                canvas.create_text(27,25,text=text,fill=color,font=self.editor.caption_font)

    def show_tip(self,owner):
        self.hide_tip();button=self.buttons[owner]
        self.tooltip=tk.Toplevel(self.root);self.tooltip.overrideredirect(True)
        tk.Label(self.tooltip,text=self.caption(owner),background='#253342',foreground='#f0f6fc',padx=10,pady=6).pack()
        self.tooltip.geometry('+%d+%d'%(button.winfo_rootx()+58,button.winfo_rooty()+8))

    def hide_tip(self):
        if self.tooltip is not None:
            try:self.tooltip.destroy()
            except tk.TclError:pass
            self.tooltip=None

    def toggle(self,owner):
        self.hide_tip()
        if self.visible and self.active==owner:self.hide()
        else:self.open(owner)

    def reveal(self,owner):
        if owner not in self.hosts:return
        for host in self.hosts.values():host.pack_forget()
        self.hosts[owner].pack(fill='both',expand=True)
        if not self.visible:self.panes.insert(0,self.panel,weight=0)
        self.visible=True;self.active=owner;self.title.configure(text=self.caption(owner));self.fit();self.draw()

    def start_loading(self,message,parent=None):
        api=self.editor.plugin_apis.get('loading_indicator')
        if api:
            try:return (api,api.call_service('loading_indicator.start',message=message,parent=parent))
            except LookupError:pass
        if parent is not None:
            ttk.Label(parent,text=message,style='Muted.TLabel').pack(anchor='w',padx=14,pady=10)
            progress=ttk.Progressbar(parent,mode='indeterminate');progress.pack(fill='x',padx=14,pady=6);progress.start(20)
            return (progress,None)
        return (None,None)

    def stop_loading(self,handle,painted=False):
        owner,token=handle
        if owner is not None:
            try:
                if token is None:owner.stop()
                elif painted:
                    try:owner.call_service('loading_indicator.finish',token)
                    except LookupError:owner.call_service('loading_indicator.stop',token)
                else:owner.call_service('loading_indicator.stop',token)
            except (LookupError,tk.TclError):pass

    def cancel_pending(self,owner):
        record=self.pending.get(owner)
        if record is None or record.get('building'):return
        self.pending.pop(owner)
        if record['job'] is not None:self.root.after_cancel(record['job'])
        self.stop_loading(record['loading'])
        placeholder=record.get('placeholder')
        if placeholder is not None and placeholder.winfo_exists():placeholder.destroy()
        if not record.get('built'):
            host=self.hosts.pop(owner,None)
            if host is not None and host.winfo_exists():host.destroy()

    def open(self,owner,save=True):
        if self.closed:return
        api=self.editor.plugin_apis.get(owner)
        if api is None:return
        for other in list(self.pending):
            if other!=owner:self.cancel_pending(other)
        if owner not in self.pins:
            self.pins.append(owner);self.active=owner;self.refresh()
            if save:self.editor.save_settings()
            return
        old=self.hosts.get(owner)
        if old is not None and owner not in self.pending and api._panel_factory is not None and not old.winfo_children() and not getattr(old,'_panel_widget',None):
            old.destroy();self.hosts.pop(owner)
        if owner not in self.hosts:
            host=ttk.Frame(self.body);host._on_reveal=lambda:self.reveal(owner);host._on_hide=self.hide
            self.hosts[owner]=host
            self.reveal(owner)
            placeholder=ttk.Frame(host,padding=(12,18));placeholder.place(x=0,y=0,relwidth=1,relheight=1)
            loading=self.start_loading('Opening '+self.caption(owner)+'…',parent=placeholder)
            record={'job':None,'loading':loading,'building':False,'built':False,'placeholder':placeholder}
            self.pending[owner]=record
            def build():
                record['job']=None;record['building']=True
                if self.closed or not host.winfo_exists():
                    self.stop_loading(loading);self.pending.pop(owner,None);return
                try:
                    if api._panel_factory is not None:host._panel_widget=api._panel_factory(host)
                    children=[child for child in host.winfo_children() if child is not placeholder]
                    if not children and getattr(host,'_panel_widget',None) is None:
                        ttk.Label(host,text='Choose an action for '+self.caption(owner)+'.',wraplength=300,style='Muted.TLabel').pack(fill='x',padx=14,pady=14)
                        for label,callback in api._commands:
                            ttk.Button(host,text=label,command=lambda cb=callback,name=owner:self.editor.run_plugin(name,cb)).pack(fill='x',padx=14,pady=4)
                except Exception as error:
                    ttk.Label(host,text='This panel could not open: '+str(error),wraplength=300,style='Muted.TLabel').pack(padx=14,pady=14)
                finally:
                    record['building']=False;record['built']=True
                    if placeholder.winfo_exists():placeholder.lift()
                    record['job']=self.root.after_idle(lambda:self.wait_panel_ready(owner,host,record))
            # Paint the loading surface before the first widget factory runs.
            def paint_then_build():
                if self.closed or not host.winfo_exists():return
                if not placeholder.winfo_ismapped():
                    record['job']=self.root.after(16,paint_then_build);return
                placeholder.update_idletasks()
                record['job']=self.root.after(120,build)
            record['job']=self.root.after_idle(paint_then_build)
        else:
            self.reveal(owner)
            for child in self.hosts[owner].winfo_children():
                if getattr(child,'_docked_panel',False):child.pack(fill='both',expand=True)
            host=self.hosts[owner];widget=getattr(host,'_panel_widget',None)
            if owner not in self.pending and widget is not None and widget.winfo_exists() and not getattr(widget,'_loading_ready',True):
                placeholder=ttk.Frame(host,padding=(12,18));placeholder.place(x=0,y=0,relwidth=1,relheight=1);placeholder.lift()
                record={'job':None,'loading':self.start_loading('Opening '+self.caption(owner)+'…',parent=placeholder),
                        'building':False,'built':True,'placeholder':placeholder}
                self.pending[owner]=record
                record['job']=self.root.after_idle(lambda:self.wait_panel_ready(owner,host,record))
        if save:self.editor.save_settings()

    def wait_panel_ready(self,owner,host,record):
        record['job']=None
        if self.pending.get(owner) is not record:return
        placeholder=record['placeholder']
        def finish():
            record['job']=None
            if self.pending.get(owner) is not record:return
            if placeholder.winfo_exists():placeholder.destroy()
            self.stop_loading(record['loading']);self.pending.pop(owner,None)
        if self.closed or not host.winfo_exists():finish();return
        widget=getattr(host,'_panel_widget',None)
        if widget is not None and widget.winfo_exists() and not getattr(widget,'_loading_ready',True):
            record['job']=self.root.after(16,lambda:self.wait_panel_ready(owner,host,record));return
        if placeholder.winfo_exists():placeholder.lift()
        self.root.update_idletasks()
        if self.pending.get(owner) is record:record['job']=self.root.after(16,finish)

    def defer_window(self,owner,callback,invoke):
        if self.closed:return False
        name=getattr(callback,'__name__','')
        if name not in ('show','show_settings','preferences','show_help','help'):return False
        if callback in self.window_jobs:return True
        if callback in self.opened_windows:return False
        instance=getattr(callback,'__self__',None)
        window=getattr(instance,'window',None)
        if name=='show' and window is not None and window.winfo_exists():return False
        self.opened_windows.add(callback)
        loading=self.start_loading('Opening '+self.caption(owner)+'…')
        record={'job':None,'loading':loading};self.window_jobs[callback]=record
        def run():
            record['job']=None
            try:
                if not self.closed:invoke()
            finally:
                self.stop_loading(loading,painted=True);self.window_jobs.pop(callback,None)
        record['job']=self.root.after(25,run)
        return True

    def hide(self,save=True):
        if self.active in self.pending:self.cancel_pending(self.active)
        self.hide_tip()
        if self.visible:self.panes.forget(self.panel)
        self.visible=False;self.active=None;self.draw()
        if save:self.editor.save_settings()

    def fit(self):
        if self.visible and self.panes.winfo_width()>100:
            owner=self.active;api=self.editor.plugin_apis.get(owner)
            preferred=api._panel_width if api and owner!='project_explorer' else self.width
            self.panes.sashpos(0,max(180,min(preferred,self.panes.winfo_width()-260)))

    def remember_width(self,event=None):
        if self.visible:
            self.width=max(180,min(900,self.panes.sashpos(0)))
            api=self.editor.plugin_apis.get(self.active)
            if api:api._panel_width=self.width
            self.editor.save_settings()

    def choose(self):
        self.hide_tip()
        if self.chooser is not None and self.chooser.winfo_exists():self.chooser.lift();return
        self.chooser=window=tk.Toplevel(self.root)
        window.title('Choose vertical plugin tabs');window.geometry('440x540');window.minsize(360,320)
        window.transient(self.root)
        window.bind('<Escape>',lambda event:window.destroy() or 'break')
        body=ttk.Frame(window,padding=18);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Vertical plugin tabs',style='Title.TLabel').pack(anchor='w')
        hint=ttk.Label(body,text='Choose plugins for the left rail. Enable other plugins in Plugin Manager.',style='Muted.TLabel',wraplength=390)
        hint.pack(fill='x',pady=(6,12))
        window.bind('<Configure>',lambda event:hint.configure(wraplength=max(240,event.width-36)) if event.widget is window else None)
        footer=ttk.Frame(body,style='Chrome.TFrame');footer.pack(side='bottom',fill='x',pady=(12,0))
        ttk.Separator(body).pack(side='bottom',fill='x',pady=(8,0))
        viewport=ttk.Frame(body);viewport.pack(fill='both',expand=True)
        canvas=self.chooser_canvas=tk.Canvas(viewport,background='#11161c',highlightthickness=0,takefocus=True)
        scrollbar=ttk.Scrollbar(viewport,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        rows=ttk.Frame(canvas,padding=(2,3));item=canvas.create_window((0,0),window=rows,anchor='nw')
        rows.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
        def wheel(event):
            direction=-1 if getattr(event,'num',0)==4 or getattr(event,'delta',0)>0 else 1
            canvas.yview_scroll(direction,'units');return 'break'
        def bind_scroll(widget):
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):widget.bind(sequence,wheel)
        bind_scroll(canvas);bind_scroll(rows)
        canvas.bind('<Next>',lambda event:canvas.yview_scroll(1,'pages') or 'break')
        canvas.bind('<Prior>',lambda event:canvas.yview_scroll(-1,'pages') or 'break')
        self.choices={}
        count=ttk.Label(footer,style='Muted.TLabel');count.pack(side='left')
        def update_count(*args):count.configure(text=f'{sum(value.get() for value in self.choices.values())} selected')
        def show_focused(widget):
            canvas.update_idletasks()
            height=max(1,rows.winfo_height());top=canvas.canvasy(0);bottom=top+canvas.winfo_height()
            y=widget.winfo_y()
            if y<top:canvas.yview_moveto(y/height)
            elif y+widget.winfo_height()>bottom:canvas.yview_moveto((y+widget.winfo_height()-canvas.winfo_height())/height)
        for owner in self.editor.plugin_apis:
            value=tk.BooleanVar(window,owner in self.pins);self.choices[owner]=value
            check=ttk.Checkbutton(rows,text=self.caption(owner),variable=value)
            check.pack(fill='x',anchor='w',pady=3);bind_scroll(check)
            check.bind('<FocusIn>',lambda event,widget=check:show_focused(widget))
            value.trace_add('write',update_count)
        def save():
            selected=[owner for owner,value in self.choices.items() if value.get()]
            self.pins=[owner for owner in self.pins if owner in selected]+[owner for owner in selected if owner not in self.pins]
            if self.active not in self.pins:self.hide(save=False)
            self.refresh()
            if self.editor.save_settings():window.destroy()
        ttk.Button(footer,text='Save tabs',style='Accent.TButton',command=save).pack(side='right')
        ttk.Button(footer,text='Cancel',command=window.destroy).pack(side='right',padx=8)
        update_count()

    def show_commands(self, selected_owner=None):
        """A bounded menu surface; opening a group never runs its actions."""
        previous = self.command_window
        if previous is not None and previous.winfo_exists():
            if selected_owner is None:
                previous.lift();previous.focus_set();return
            previous.destroy()
        window = self.command_window = tk.Toplevel(self.root)
        window.title('Plugins');window.transient(self.root)
        width=min(420,self.root.winfo_screenwidth()-40)
        height=min(520,self.root.winfo_screenheight()-80)
        x=min(self.root.winfo_rootx()+180,self.root.winfo_screenwidth()-width)
        y=min(self.root.winfo_rooty()+30,self.root.winfo_screenheight()-height)
        window.geometry(f'{width}x{height}+{max(0,x)}+{max(0,y)}')
        window.resizable(False,False);window.configure(background='#11161c')
        window.bind('<Escape>',lambda event:window.destroy())
        heading=ttk.Frame(window,padding=10);heading.pack(fill='x')
        title=ttk.Label(heading,text='Plugins',style='Title.TLabel');title.pack(side='left')
        ttk.Button(heading,text='×',width=3,command=window.destroy).pack(side='right')
        viewport=ttk.Frame(window);viewport.pack(fill='both',expand=True,padx=8,pady=(0,8))
        canvas=self.command_canvas=tk.Canvas(viewport,background='#11161c',highlightthickness=0)
        scrollbar=ttk.Scrollbar(viewport,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        rows=ttk.Frame(canvas);item=canvas.create_window((0,0),window=rows,anchor='nw')
        rows.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
        def wheel(event):
            direction=-1 if getattr(event,'num',None)==4 or getattr(event,'delta',0)>0 else 1
            canvas.yview_scroll(direction*3,'units');return 'break'
        def bind_scroll(widget):
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):widget.bind(sequence,wheel)
        bind_scroll(canvas);bind_scroll(rows)
        self.command_buttons=[]
        def render(owner=None):
            for child in rows.winfo_children():child.destroy()
            self.command_buttons=[];canvas.yview_moveto(0)
            title.configure(text=self.caption(owner) if owner else 'Plugins')
            def add(label,callback,close=False):
                def invoke():
                    if close:window.destroy()
                    callback()
                button=ttk.Button(rows,text=label,command=invoke)
                button.pack(fill='x',padx=4,pady=3);bind_scroll(button)
                def focus(event):
                    canvas.update_idletasks()
                    top=button.winfo_y();bottom=top+button.winfo_height()
                    if top<canvas.canvasy(0):canvas.yview_moveto(top/max(1,rows.winfo_height()))
                    elif bottom>canvas.canvasy(canvas.winfo_height()):canvas.yview_moveto((bottom-canvas.winfo_height())/max(1,rows.winfo_height()))
                button.bind('<FocusIn>',focus);self.command_buttons.append(button)
            if owner:
                add('‹ All plugins',lambda:render())
                add('Open vertical tab',lambda:self.open(owner),True)
                for key,label,callback in self.editor.plugin_commands:
                    if key.split(':',1)[0]==owner:add(label,callback,True)
            else:
                add('Choose vertical plugin tabs…',self.choose,True)
                last=self.editor.plugins.index('end')
                for index in range((last+1) if last is not None else 0):
                    if self.editor.plugins.type(index)=='separator':break
                    if self.editor.plugins.entrycget(index,'state')!='disabled':
                        add(self.editor.plugins.entrycget(index,'label'),lambda i=index:self.editor.plugins.invoke(i),True)
                for name in self.editor.plugin_apis:
                    add(self.caption(name)+'  ›',lambda name=name:render(name))
            if self.command_buttons:self.command_buttons[0].focus_set()
        render(selected_owner);window.lift()

    def settings(self):
        return {'sidebar_plugins':self.pins,'sidebar_active':self.active,'sidebar_width':self.width}

    def cleanup(self):
        self.closed=True
        if self.command_window is not None and self.command_window.winfo_exists():self.command_window.destroy()
        for owner in list(self.pending):self.cancel_pending(owner)
        for record in self.window_jobs.values():
            if record['job'] is not None:self.root.after_cancel(record['job'])
            self.stop_loading(record['loading'])
        self.window_jobs.clear()
        self.hide_tip()
        if self.chooser is not None and self.chooser.winfo_exists():self.chooser.destroy()
