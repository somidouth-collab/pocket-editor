"""FusionPHP .fpf themes, safe formatting, context completion and syntax squiggles."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import re
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from pocket_editor_plugins import _fusionphp as fpf, _loading
from pocket_editor_plugins._docking import create_window
from pocket_editor_plugins._diagnostic_marks import DiagnosticMarks
from pocket_editor_plugins._php_completion import prepare_completion
from pocket_editor_plugins.code_theme import THEMES

DEFAULTS={'highlighting':True,'automatic':True,'syntax_automatic':True,'delay_ms':220,'indent':4,'mode':'Auto','fields':[]}


def read_settings(path):
    result=copy.deepcopy(DEFAULTS)
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value,dict):return result
        for key in ('highlighting','automatic','syntax_automatic'):
            if type(value.get(key)) is bool:result[key]=value[key]
        for key,low,high in (('delay_ms',100,1500),('indent',1,8)):
            if type(value.get(key)) is int and low<=value[key]<=high:result[key]=value[key]
        if value.get('mode') in ('Auto','Page','Layout'):result['mode']=value['mode']
        if isinstance(value.get('fields'),list):result['fields']=[field for field in value['fields'][:500] if isinstance(field,str) and re.fullmatch(fpf.FIELD,field)]
    except (OSError,ValueError):pass
    return result


def perform(kind,capture,settings,root):
    path,source,cursor=capture[:3]
    if kind=='format':return fpf.format_source(source,path,settings['indent'],settings['mode'])
    if kind=='check':return fpf.check_source(source,path,settings['mode'])
    return fpf.completion_items(source,cursor,path,root,settings['mode'],settings['indent'],settings['fields'])


class FusionPHPTools:
    def __init__(self,api):
        self.api,self.editor=api,api._editor;self.root,self.text=self.editor.root,self.editor.buffer
        self.settings_path=Path(self.editor.settings_path).with_name('fusionphp_tools_settings.json')
        self.settings=read_settings(self.settings_path);self.closed=False
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pocket-fpf')
        self.jobs={};self.sequence=0;self.pending_check=self.pending_complete=None
        self.window=self.settings_window=self.popup=self.listing=self.issues=None
        self.highlight_job=None;self.highlight_signature=None
        self.items=[];self.completion_capture=self.checked_capture=None;self.diagnostics=[]
        self.status=tk.StringVar(self.root,'Open a FusionPHP .fpf template. No PHP server is required.')
        self.status_trace=self.status.trace_add('write',self.status_changed)
        self.marks=DiagnosticMarks(self.editor,self.jump)
        self.bindings=[]
        for event,callback in (('<Control-space>',self.complete),('<KeyRelease>',self.key_released),
            ('<Return>',self.accept),('<Tab>',self.accept),('<Escape>',self.escape),
            ('<Up>',lambda event:self.move(-1)),('<Down>',lambda event:self.move(1)),
            ('<FocusOut>',lambda event:self.dismiss()),('<ButtonRelease-1>',lambda event:self.dismiss())):
            self.bindings.append((event,self.text.bind(event,callback,add='+')))
        api.on('document_changed',self.changed);api.on('document_switched',self.changed)
        api.on('document_saved',self.changed);api.on('project_changed',self.changed)
        api.on_cleanup(self.cleanup);api.add_panel(self.mount,width=390)
        api.add_command('FusionPHP Tools',self.show)
        api.add_command('Format FusionPHP Template',self.format_document)
        api.add_command('FusionPHP Autocomplete',self.complete)
        api.add_command('Check FusionPHP Syntax',self.check)
        api.add_command('FusionPHP Settings',self.preferences)
        api.add_command('FusionPHP Theme',self.theme)
        api.add_command('FusionPHP Help',self.help)
        api.provide_service('format',self.format_document)
        api.provide_service('complete',self.complete)
        api.provide_service('check_syntax',self.check)
        api.provide_service('highlight',self.paint_highlighting)
        self.poll_job=self.root.after(100,self.poll)

    def palette(self):
        background=self.text.cget('background').lower()
        name=next((name for name,values in THEMES.items() if values[0].lower()==background),None)
        if name is None:
            rgb=self.text.winfo_rgb(background)
            name='Light' if sum(rgb)/3>32767 else 'Dark'
        bg,fg,selection,muted,keyword,string,comment,number,function=THEMES[name]
        field={'Dark':'#9cdcfe','Light':'#0550ae','Dracula':'#8be9fd','Solarized Dark':'#268bd2'}[name]
        directive={'Dark':'#c586c0','Light':'#8250df','Dracula':'#ff79c6','Solarized Dark':'#859900'}[name]
        return dict(tag=keyword,attribute=field,field=field,function=function,keyword=directive,
                    string=string,comment=comment,number=number)

    def clear_highlighting(self,all_documents=False):
        widgets=[document['widget'] for document in self.editor.documents] if all_documents else [self.text.widget]
        for widget in widgets:
            if not widget.winfo_exists():continue
            for kind in ('tag','attribute','field','function','keyword','string','comment','number'):
                widget.tag_remove('fpf_syntax_'+kind,'1.0','end')
        self.highlight_signature=None

    def paint_highlighting(self):
        if self.closed:return
        signature=(str(self.text.widget),self.api.get_path(),self.editor.document_generation,self.editor.document_revision,
                   self.text.cget('background'),self.settings['highlighting'])
        if signature==self.highlight_signature:return
        self.clear_highlighting();self.highlight_signature=signature
        if not self.settings['highlighting'] or not fpf.is_fpf(self.api.get_path()):return
        source=self.api.get_text()
        if len(source)>fpf.MAX_SOURCE:return
        positions=fpf.TextPositions(source);ranges={}
        for kind,start,end in fpf.highlight_spans(source):
            ranges.setdefault(kind,[]).extend((positions.index(start),positions.index(end)))
        colors=self.palette()
        # Distinct names avoid legacy Code Theme removing these ranges. Raise them
        # above restored white foreground tags while leaving the selection on top.
        for kind in ('string','tag','attribute','number','field','function','keyword','comment'):
            name='fpf_syntax_'+kind;self.text.tag_configure(name,foreground=colors[kind])
            indexes=ranges.get(kind,[])
            for offset in range(0,len(indexes),1000):self.text.tag_add(name,*indexes[offset:offset+1000])
            self.text.tag_raise(name)
        self.text.tag_raise('sel')

    def schedule_highlighting(self):
        self.cancel_timer('highlight_job')
        def paint():self.highlight_job=None;self.paint_highlighting()
        self.highlight_job=self.root.after_idle(paint)

    def status_changed(self,*args):
        if not self.closed and fpf.is_fpf(self.api.get_path()):self.editor.status.configure(text='FusionPHP · '+self.status.get().split('\n')[0][:220])

    def capture(self):
        return (self.api.get_path(),self.api.get_text(),len(self.text.get('1.0','insert')),
                self.editor.document_generation,self.editor.document_revision,self.text.index('insert'))

    def matches(self,capture,cursor=False):
        return (capture is not None and self.api.get_path()==capture[0] and
            self.editor.document_generation==capture[3] and self.editor.document_revision==capture[4] and
            self.api.get_text()==capture[1] and (not cursor or self.text.index('insert')==capture[5]))

    def project(self,path):
        try:selected=self.api.call_service('project_explorer.get_project')
        except LookupError:selected=None
        return fpf.find_project(path,selected)

    def request(self,kind,manual=True):
        if self.closed:return
        if not fpf.is_fpf(self.api.get_path()):
            if manual:self.status.set('Open or save a .fpf template first.');self.show()
            return
        capture=self.capture()
        if len(capture[1])>fpf.MAX_SOURCE:self.status.set('FusionPHP tools pause above 300,000 characters.');return
        if kind=='format' and any(job[0]=='format' for job in self.jobs.values()):return
        for ticket,job in list(self.jobs.items()):
            if job[0]==kind:
                job[2].cancel();_loading.cancel(self.api,job[3]);self.jobs.pop(ticket)
        if len(self.jobs)>=4:return
        self.sequence+=1
        future=self.executor.submit(perform,kind,capture,copy.deepcopy(self.settings),self.project(capture[0]))
        token=_loading.begin(self.api,{'format':'Formatting FusionPHP template…','check':'Checking FusionPHP syntax…','complete':'Loading FusionPHP suggestions…'}[kind])
        self.jobs[self.sequence]=(kind,capture,future,token,manual)
        if manual:self.status.set({'format':'Formatting…','check':'Checking syntax…','complete':'Loading suggestions…'}[kind])

    def format_document(self):self.request('format')
    def check(self):self.request('check')
    def complete(self,event=None):
        if not fpf.is_fpf(self.api.get_path()):return None
        self.request('complete');return 'break'

    def cancel_timer(self,name):
        value=getattr(self,name)
        if value is not None:self.root.after_cancel(value);setattr(self,name,None)

    def schedule_check(self):
        self.cancel_timer('pending_check')
        if self.settings['syntax_automatic'] and fpf.is_fpf(self.api.get_path()):
            def run():self.pending_check=None;self.request('check',False)
            self.pending_check=self.root.after(500,run)

    def changed(self,**event):
        self.dismiss();self.schedule_highlighting()
        if not self.matches(self.checked_capture):self.clear_issues()
        self.schedule_check()

    def key_released(self,event):
        if not fpf.is_fpf(self.api.get_path()):return
        if event.keysym in ('Up','Down','Left','Right','Return','Tab','Escape','Control_L','Control_R','Shift_L','Shift_R'):return
        self.dismiss();self.cancel_timer('pending_complete');self.schedule_check()
        before=self.text.get('1.0','insert')
        context=(before.rfind('{{')>before.rfind('}}') or before.rfind('{%')>before.rfind('%}') or before.rfind('<')>before.rfind('>'))
        if self.settings['automatic'] and context:
            def run():self.pending_complete=None;self.request('complete',False)
            self.pending_complete=self.root.after(self.settings['delay_ms'],run)

    def dismiss(self):
        if self.popup is not None and self.popup.winfo_exists():self.popup.withdraw()
        self.highlight_job=None;self.highlight_signature=None
        self.items=[];self.completion_capture=None

    def escape(self,event=None):
        if self.popup is not None and self.popup.winfo_viewable():self.dismiss();return 'break'

    def move(self,direction):
        if self.popup is None or not self.popup.winfo_viewable():return None
        selection=self.listing.curselection();index=min(len(self.items)-1,max(0,(selection[0] if selection else 0)+direction))
        self.listing.selection_clear(0,'end');self.listing.selection_set(index);self.listing.see(index);return 'break'

    def suggestions(self,items,capture):
        self.dismiss()
        if not items:self.status.set('No matching FusionPHP suggestions. Add custom data fields in Settings if needed.');return
        if self.popup is None or not self.popup.winfo_exists():
            self.popup=tk.Toplevel(self.root);self.popup.overrideredirect(True);self.popup.transient(self.root)
            frame=ttk.Frame(self.popup,padding=4);frame.pack(fill='both',expand=True)
            self.listing=tk.Listbox(frame,width=42,height=8,font=self.editor.ui_font,background='#11161c',foreground='#d1dae4',
                selectbackground='#1597f5',selectforeground='white',exportselection=False,takefocus=False,highlightthickness=0)
            self.listing.pack(side='left',fill='both',expand=True)
            scroll=ttk.Scrollbar(frame,command=self.listing.yview);scroll.pack(side='right',fill='y');self.listing.configure(yscrollcommand=scroll.set)
            def click(event):
                index=self.listing.nearest(event.y);self.listing.selection_clear(0,'end');self.listing.selection_set(index);self.accept();return 'break'
            self.listing.bind('<ButtonRelease-1>',click)
        self.items=items;self.completion_capture=capture;self.listing.delete(0,'end')
        for item in items:self.listing.insert('end',item['label'])
        self.listing.selection_set(0);box=self.text.bbox('insert')
        if not box:self.dismiss();return
        self.popup.update_idletasks()
        x=min(self.text.winfo_rootx()+box[0],self.root.winfo_screenwidth()-self.popup.winfo_reqwidth())
        y=self.text.winfo_rooty()+box[1]+box[3]
        if y+self.popup.winfo_reqheight()>self.root.winfo_screenheight():y-=box[3]+self.popup.winfo_reqheight()
        self.popup.geometry(f'+{max(0,x)}+{max(0,y)}');self.popup.deiconify();self.popup.lift()
        self.status.set(f'{len(items)} suggestions · Enter/Tab accepts · Escape closes')

    def accept(self,event=None):
        if self.popup is None or not self.popup.winfo_viewable():return None
        capture=self.completion_capture
        if not self.matches(capture,True):self.dismiss();return 'break'
        selected=self.listing.curselection();item=self.items[selected[0] if selected else 0]
        try:edits,caret,stops=prepare_completion(capture[1],capture[2],item)
        except (KeyError,TypeError,ValueError) as error:self.status.set(str(error));return 'break'
        self.dismiss();self.text.edit_separator()
        for start,end,value,primary in reversed(edits):self.text.replace(fpf.text_index(capture[1],start),fpf.text_index(capture[1],end),value)
        self.text.edit_separator();source=self.api.get_text();self.text.mark_set('insert',fpf.text_index(source,caret))
        self.text.tag_remove('sel','1.0','end')
        if stops and stops[0][2]>stops[0][1]:self.text.tag_add('sel',fpf.text_index(source,stops[0][1]),fpf.text_index(source,stops[0][2]))
        self.editor.refresh();self.schedule_check();return 'break'

    def clear_issues(self):
        self.marks.clear();self.diagnostics=[];self.checked_capture=None
        if self.issues is not None and self.issues.winfo_exists():self.issues.delete(*self.issues.get_children())

    def set_issues(self,issues,capture):
        self.clear_issues();self.diagnostics=list(issues);self.checked_capture=capture
        self.render_issues();self.marks.show(capture[1],issues,lambda:self.matches(capture))
        self.status.set('Syntax OK · FusionPHP template' if not issues else f"Line {issues[0]['line']}: {issues[0]['message']}")

    def render_issues(self):
        if self.issues is not None and self.issues.winfo_exists():
            self.issues.delete(*self.issues.get_children())
            for index,issue in enumerate(self.diagnostics):self.issues.insert('', 'end',iid=str(index),values=(issue['line'],issue['message']))

    def jump(self,issue=None):
        if not self.matches(self.checked_capture):self.clear_issues();return
        if issue is None:
            selection=self.issues.selection() if self.issues is not None else ()
            if not selection:return
            issue=self.diagnostics[int(selection[0])]
        self.text.mark_set('insert',fpf.text_index(self.checked_capture[1],issue['start']));self.text.see('insert');self.text.focus_set();self.status.set(issue['message'])

    def mount(self,parent):self.show(parent);return self.window
    def show(self,parent=None):
        if self.closed:return
        if parent is None:self.editor.plugin_sidebar.open('fusionphp_tools');return
        if self.window is not None and self.window.winfo_exists():
            if self.window.master is parent:self.window.deiconify();return
            self.window.destroy()
        self.window=create_window(self.root,parent)
        body=ttk.Frame(self.window,padding=10);body.pack(fill='both',expand=True)
        ttk.Label(body,text='FUSIONPHP · .FPF',style='Title.TLabel').pack(anchor='w',pady=(0,8))
        controls=ttk.Frame(body);controls.pack(fill='x')
        for index,(label,callback) in enumerate((('Format',self.format_document),('Check syntax',self.check),('Autocomplete',self.complete),('Settings',self.preferences),('Theme',self.theme),('How to use',self.help))):
            ttk.Button(controls,text=label,command=callback).grid(row=index//2,column=index%2,sticky='ew',padx=2,pady=3)
        controls.columnconfigure(0,weight=1);controls.columnconfigure(1,weight=1)
        ttk.Label(body,text='Errors are underlined in red. Double-click a message to jump to its source.',style='Muted.TLabel',wraplength=300).pack(fill='x',pady=10)
        frame=ttk.Frame(body);frame.pack(fill='both',expand=True)
        self.issues=ttk.Treeview(frame,columns=('line','message'),show='headings',height=7)
        self.issues.heading('line',text='Line');self.issues.column('line',width=45,stretch=False)
        self.issues.heading('message',text='Message');self.issues.column('message',width=235,stretch=True)
        scroll=ttk.Scrollbar(frame,command=self.issues.yview);scroll.pack(side='right',fill='y');self.issues.configure(yscrollcommand=scroll.set)
        self.issues.pack(fill='both',expand=True);self.issues.bind('<Double-1>',lambda event:self.jump())
        label=ttk.Label(body,textvariable=self.status,style='Muted.TLabel',wraplength=300);label.pack(fill='x',pady=(8,0))
        body.bind('<Configure>',lambda event:label.configure(wraplength=max(100,event.width-20)))
        self.render_issues()

    def theme(self):
        try:self.api.call_service('code_theme.settings')
        except LookupError:
            provider=self.editor.plugin_apis.get('code_theme')
            command=next((callback for label,callback in provider._commands if label=='Code Theme'),None) if provider else None
            if command:self.editor.run_plugin('code_theme.py',command)
            else:self.status.set('FusionPHP colors are active. Enable Code Theme to choose an editor palette.')

    def preferences(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():self.settings_window.lift();return
        window=self.settings_window=tk.Toplevel(self.root);window.title('FusionPHP Settings');window.geometry('460x490');window.minsize(350,320)
        footer=ttk.Frame(window,padding=10);footer.pack(side='bottom',fill='x')
        body=ttk.Frame(window,padding=14);body.pack(fill='both',expand=True)
        highlighting=tk.BooleanVar(window,self.settings['highlighting'])
        ttk.Checkbutton(body,text='Color FusionPHP templates',variable=highlighting).pack(anchor='w',pady=4)
        auto=tk.BooleanVar(window,self.settings['automatic']);syntax=tk.BooleanVar(window,self.settings['syntax_automatic'])
        indent=tk.StringVar(window,str(self.settings['indent']));delay=tk.StringVar(window,str(self.settings['delay_ms']));mode=tk.StringVar(window,self.settings['mode'])
        ttk.Checkbutton(body,text='Automatic template suggestions',variable=auto).pack(anchor='w')
        ttk.Checkbutton(body,text='Check syntax after typing',variable=syntax).pack(anchor='w',pady=(0,8))
        for title,variable,low,high in (('Indent spaces',indent,1,8),('Completion delay (ms)',delay,100,1500)):
            row=ttk.Frame(body);row.pack(fill='x',pady=4);ttk.Label(row,text=title).pack(side='left')
            ttk.Spinbox(row,textvariable=variable,from_=low,to=high,width=8).pack(side='right')
        row=ttk.Frame(body);row.pack(fill='x',pady=4);ttk.Label(row,text='Template type').pack(side='left')
        ttk.Combobox(row,textvariable=mode,values=('Auto','Page','Layout'),state='readonly',width=12).pack(side='right')
        ttk.Label(body,text='Additional data fields (one dotted field per line)',wraplength=320).pack(anchor='w',pady=(8,4))
        fields=tk.Text(body,height=5,width=30,background='#11161c',foreground='#d1dae4',insertbackground='#d1dae4');fields.pack(fill='both',expand=True)
        fields.insert('1.0','\n'.join(self.settings['fields']))
        def save():
            try:
                width=int(indent.get());milliseconds=int(delay.get());names=[value.strip() for value in fields.get('1.0','end-1c').splitlines() if value.strip()]
                if not 1<=width<=8 or not 100<=milliseconds<=1500 or len(names)>500 or any(not re.fullmatch(fpf.FIELD,value) for value in names):raise ValueError
            except ValueError:messagebox.showerror('FusionPHP Settings','Use 1–8 indent spaces, 100–1500 ms, and valid dotted field names.',parent=window);return
            value=dict(highlighting=highlighting.get(),automatic=auto.get(),syntax_automatic=syntax.get(),delay_ms=milliseconds,indent=width,mode=mode.get(),fields=names)
            try:self.editor.write_data(self.settings_path,json.dumps(value,indent=2).encode())
            except OSError as error:messagebox.showerror('FusionPHP Settings',str(error),parent=window);return
            self.settings=read_settings(self.settings_path);window.destroy();self.paint_highlighting();self.schedule_check();self.status.set('FusionPHP settings saved.')
        ttk.Button(footer,text='How to use',command=self.help).pack(side='left')
        ttk.Button(footer,text='Save Settings',command=save,style='Accent.TButton').pack(side='right')

    def help(self):
        path=Path(__file__).resolve().parents[1]/'docs/fusionphp.html'
        if path.is_file():webbrowser.open(path.as_uri())
        else:messagebox.showinfo('FusionPHP Tools','Open a .fpf file. Ctrl+Space suggests directives and fields. Check syntax shows red zigzag errors. Format changes only the buffer; Undo reverts it. Save writes the file. Auto mode treats app/layouts/*.fpf as layouts. Theme uses Code Theme.',parent=self.root)

    def poll(self):
        if self.closed:return
        for ticket,job in list(self.jobs.items()):
            kind,capture,future,token,manual=job
            if not future.done():continue
            self.jobs.pop(ticket,None)
            try:
                value=future.result()
                if not self.matches(capture,kind=='complete'):continue
                if kind=='format':
                    if value!=capture[1]:
                        cursor=self.text.index('insert');view=self.text.yview()[0]
                        self.api.replace_text(value);self.text.mark_set('insert',cursor);self.text.yview_moveto(view)
                        self.editor.refresh();self.api.emit('document_formatted',language='FusionPHP')
                    self.clear_issues();self.schedule_check();self.status.set('Template formatted. Undo reverts; Save writes the file.')
                elif kind=='check':
                    self.set_issues(value,capture)
                    if manual and value:self.show()
                else:self.suggestions(value,capture)
            except Exception as error:
                if self.matches(capture):
                    if isinstance(error,fpf.TemplateError):
                        offset=error.start;self.set_issues([dict(start=offset,end=error.end,line=capture[1].count('\n',0,offset)+1,
                            column=offset-capture[1].rfind('\n',0,offset),severity='error',message=str(error))],capture)
                    self.status.set(str(error).split('\n')[0][:300])
                    if manual:messagebox.showerror('FusionPHP Tools',str(error)[:2000]+'\n\nYour file was left unchanged.',parent=self.root)
            finally:
                _loading.end(self.api,token,ready=lambda:self.closed or 'fusionphp_tools' not in self.editor.plugin_sidebar.pending)
        if self.checked_capture is not None:
            if self.matches(self.checked_capture):self.marks.draw()
            else:self.clear_issues()
        self.paint_highlighting()
        self.poll_job=self.root.after(100,self.poll)

    def cleanup(self):
        if self.closed:return
        self.closed=True;self.cancel_timer('pending_check');self.cancel_timer('pending_complete');self.cancel_timer('highlight_job')
        self.clear_highlighting(all_documents=True)
        self.root.after_cancel(self.poll_job)
        for kind,capture,future,token,manual in self.jobs.values():future.cancel();_loading.cancel(self.api,token)
        self.jobs.clear();self.executor.shutdown(wait=False,cancel_futures=True);self.marks.clear()
        for event,ident in self.bindings:self.text.unbind(event,ident)
        self.status.trace_remove('write',self.status_trace)
        for window in (self.popup,self.settings_window,self.window):
            if window is not None and window.winfo_exists():window.destroy()


def register(api):FusionPHPTools(api)
