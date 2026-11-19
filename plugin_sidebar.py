"""Persistent vertical plugin tabs and docked panels for Pocket Editor."""
import tkinter as tk
from tkinter import ttk

TITLES = {'project_explorer':'Project Explorer','project_terminal':'Terminal','git_project':'Git',
          'ai_assistant':'AI Assistant','codex_assistant':'Codex','team_project':'Team Project',
          'python_tools':'Python Tools','php_tools':'PHP Tools'}
DEFAULT_PINS = ['project_explorer','search_replace','project_terminal','git_project','ai_assistant','codex_assistant','team_project']


class PluginSidebar:
    def __init__(self, editor, parent, settings):
        self.editor, self.root = editor, editor.root
        self.active = settings.get('sidebar_active', 'project_explorer')
        self.width = settings.get('sidebar_width', 320)
        self.pins = list(settings.get('sidebar_plugins', DEFAULT_PINS))
        self.hosts, self.buttons, self.visible = {}, {}, False
        self.tooltip = self.chooser = None
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
            button.bind('<Button-1>',lambda event,owner=owner:self.toggle(owner))
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
                text={'ai_assistant':'AI','codex_assistant':'CX','git_project':'Git','team_project':'TM','python_tools':'Py','php_tools':'PHP'}.get(owner,owner[:2].upper())
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

    def open(self,owner,save=True):
        api=self.editor.plugin_apis.get(owner)
        if api is None:return
        if owner not in self.pins:
            self.pins.append(owner)
            self.active=owner
            self.refresh()
            if save:self.editor.save_settings()
            return
        old=self.hosts.get(owner)
        if old is not None and api._panel_factory is not None and not old.winfo_children() and not getattr(old,'_panel_widget',None):
            old.destroy();self.hosts.pop(owner)
        if owner not in self.hosts:
            host=ttk.Frame(self.body);host._on_reveal=lambda:self.reveal(owner)
            host._on_hide=self.hide
            self.hosts[owner]=host
            try:
                if api._panel_factory is not None:host._panel_widget=api._panel_factory(host)
            except Exception as error:
                ttk.Label(host,text='This panel could not open: '+str(error),wraplength=300).pack(padx=12,pady=12)
            if not host.winfo_children() and getattr(host,"_panel_widget",None) is None:
                ttk.Label(host,text='Choose an action for '+self.caption(owner)+'.',wraplength=300,style='Muted.TLabel').pack(fill='x',padx=14,pady=14)
                for label,callback in api._commands:
                    ttk.Button(host,text=label,command=lambda cb=callback,name=owner:self.editor.run_plugin(name,cb)).pack(fill='x',padx=14,pady=4)
        self.reveal(owner)
        for child in self.hosts[owner].winfo_children():
            if getattr(child,'_docked_panel',False):child.pack(fill='both',expand=True)
        if save:self.editor.save_settings()

    def hide(self,save=True):
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
        self.chooser=window=tk.Toplevel(self.root);window.title('Choose vertical plugin tabs');window.geometry('420x580')
        body=ttk.Frame(window,padding=18);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Vertical plugin tabs',style='Title.TLabel').pack(anchor='w')
        ttk.Label(body,text='Choose plugins for the left rail. Enable other plugins in Plugin Manager.',style='Muted.TLabel').pack(anchor='w',pady=(5,12))
        self.choices={}
        for owner in self.editor.plugin_apis:
            value=tk.BooleanVar(window,owner in self.pins);self.choices[owner]=value
            ttk.Checkbutton(body,text=self.caption(owner),variable=value).pack(anchor='w',pady=2)
        def save():
            selected=[owner for owner,value in self.choices.items() if value.get()]
            self.pins=[owner for owner in self.pins if owner in selected]+[owner for owner in selected if owner not in self.pins]
            if self.active not in self.pins:self.hide(save=False)
            self.refresh();self.editor.save_settings();window.destroy()
        ttk.Button(body,text='Save tabs',style='Accent.TButton',command=save).pack(anchor='e',pady=12)

    def settings(self):
        return {'sidebar_plugins':self.pins,'sidebar_active':self.active,'sidebar_width':self.width}

    def cleanup(self):
        self.hide_tip()
        if self.chooser is not None and self.chooser.winfo_exists():self.chooser.destroy()
