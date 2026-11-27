"""Web Tools: local HTML/CSS/JavaScript formatting and context-aware completion."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import re
import tkinter as tk
from tkinter import ttk
from pocket_editor_plugins import _loading
from pocket_editor_plugins._web_tools import WebWorker, language, read_settings, index
from pocket_editor_plugins._php_completion import prepare_completion
from pocket_editor_plugins._php_tools import offset_at
from pocket_editor_plugins._diagnostic_marks import DiagnosticMarks


class WebTools:
    def __init__(self, api):
        self.api,self.editor=api,api._editor;self.root,self.text=self.editor.root,self.editor.buffer
        self.settings_path=self.editor.settings_path.with_name('web_tools_settings.json')
        self.settings=read_settings(self.settings_path);self.worker=WebWorker()
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pocket-web')
        self.jobs={};self.closed=False;self.pending=None;self.popup=None;self.panel=None
        self.items=[];self.completion_capture=None;self.checked=None;self.stops=[];self.stop_number=-1;self.cancelled=set();self.issues=[]
        self.status=tk.StringVar(self.root,'HTML · CSS · JavaScript — local formatting and language services.')
        self.marks=DiagnosticMarks(self.editor,lambda issue:self.jump(issue))
        self.bindings=[]
        for sequence,callback in (('<Control-space>',self.complete),('<KeyRelease>',self.typed),
                ('<Return>',self.accept),('<Tab>',self.tab),('<Shift-Tab>',self.back_tab),('<ISO_Left_Tab>',self.back_tab),
                ('<Escape>',self.escape),('<Up>',lambda event:self.move(-1)),('<Down>',lambda event:self.move(1)),
                ('<FocusOut>',self.focus_out),('<ButtonRelease-1>',lambda event:self.dismiss()),
                (f'<{self.editor.modifier}-Shift-F>',self.format_key)):
            self.bindings.append((sequence,self.text.bind(sequence,callback,add='+')))
        api.on('document_changed',self.changed);api.on('document_switched',self.switched)
        api.on_cleanup(self.cleanup);api.add_panel(self.mount,width=360)
        for label,callback in (('Web Tools',self.show),('Format Web Document',self.format_document),
                ('Web Autocomplete',self.complete),('Check Web Syntax',lambda:self.request('check')),
                ('Install / Repair Web Tools',lambda:self.request('install'))):api.add_command(label,callback)
        for name,callback in (('format',self.format_document),('complete',self.complete),('check_syntax',lambda:self.request('check'))):api.provide_service(name,callback)
        self.poll_job=self.root.after(60,self.poll)

    def capture(self):
        return (str(self.text.widget),self.api.get_path(),self.api.get_text(),len(self.text.get('1.0','insert')),
                self.editor.document_generation,self.editor.document_revision)

    def valid(self,capture,cursor=False):
        return (capture is not None and capture[0]==str(self.text.widget) and capture[1]==self.api.get_path()
                and capture[4:]==(self.editor.document_generation,self.editor.document_revision)
                and capture[2]==self.api.get_text() and (not cursor or capture[3]==len(self.text.get('1.0','insert'))))

    def request(self,kind,manual=True):
        if self.closed:return
        capture=self.capture()
        if kind!='install' and not language(capture[1]):
            self.status.set('Save the document with an HTML, CSS, JS, JSX, TS, SCSS or LESS extension.');return
        if len(capture[2])>300000 and kind!='install':self.status.set('Web tools pause above 300,000 characters.');return
        # Bound queued work; never let automatic completion delay an explicit action.
        if any(job[0]==kind for job in self.jobs.values()):return
        if kind=='complete' and self.jobs:return
        if len(self.jobs)>=2:self.status.set('Web Tools is busy; please wait.');return
        documents=[];total=0
        for document in self.editor.documents[:32]:
            if language(document['path']):
                source=document['widget'].get('1.0','end-1c');total+=len(source)
                if total<=2000000:documents.append(dict(path=str(document['path']),source=source))
        try:project=self.api.call_service('project_explorer.get_project')
        except LookupError:project=None
        path=Path(capture[1]).resolve() if capture[1] else None
        if path and project:
            try:path.relative_to(Path(project).resolve())
            except ValueError:project=None
        payload=dict(self.settings,operation=kind,language=language(path),path=str(path) if path else '',
                     source=capture[2],offset=len(capture[2][:capture[3]].encode('utf-16-le'))//2,
                     root=str(Path(project).resolve() if project else (path.parent if path else Path.cwd())),documents=documents)
        future=self.executor.submit(self.worker.install if kind=='install' else self.worker.request,*(() if kind=='install' else (payload,)))
        token=_loading.begin(self.api,{'format':'Formatting web document…','complete':'Loading web suggestions…','check':'Checking web syntax…','install':'Installing web language tools…'}[kind]) if manual else None
        self.jobs[future]=(kind,capture,token,manual)
        if manual:self.status.set('Installing tools…' if kind=='install' else f'{kind.capitalize()} in progress…')

    def format_key(self,event=None):
        if language(self.api.get_path()):self.format_document();return 'break'

    def format_document(self):self.request('format')

    def complete(self,event=None):
        if not language(self.api.get_path()):return
        self.request('complete');return 'break'

    def focus_out(self,event=None):
        if self.popup is not None and self.popup.winfo_exists():
            x,y=self.root.winfo_pointerxy()
            if self.popup.winfo_rootx()<=x<self.popup.winfo_rootx()+self.popup.winfo_width() and self.popup.winfo_rooty()<=y<self.popup.winfo_rooty()+self.popup.winfo_height():return
        self.dismiss()

    def dismiss(self):
        self.items=[];self.completion_capture=None
        if self.popup is not None and self.popup.winfo_exists():self.popup.withdraw()

    def escape(self,event=None):
        self.cancel_timer()
        for future,job in self.jobs.items():
            if job[0]=='complete':self.cancelled.add(future)
        if self.popup is not None and self.popup.winfo_viewable():self.dismiss();return 'break'
        if self.stops:self.clear_stops();return 'break'

    def cancel_timer(self):
        if self.pending is not None:self.root.after_cancel(self.pending);self.pending=None

    def typed(self,event):
        if not language(self.api.get_path()):return
        if event.state & (4|8) or event.keysym in ('Up','Down','Left','Right','Tab','Return','Escape','Shift_L','Shift_R'):return
        self.dismiss();self.cancel_timer()
        before=self.text.get('1.0','insert')
        if self.settings['automatic'] and (re.search(r'[\w$-]{2}$',before) or before.endswith(('<','.',':',' '))):
            def run():self.pending=None;self.request('complete',False)
            self.pending=self.root.after(self.settings['delay'],run)

    def changed(self,**_event):
        self.dismiss()
        if self.checked is not None and not self.valid(self.checked):
            self.marks.clear();self.checked=None;self.issues=[]
            if self.panel is not None and self.panel.winfo_exists():self.issue_list.delete(0,'end')

    def switched(self,**_event):
        self.cancel_timer();self.clear_stops();self.changed()

    def suggestions(self,items,capture):
        self.dismiss();valid=[]
        for item in items:
            try:prepare_completion(capture[2],capture[3],item)
            except (ValueError,KeyError,TypeError):continue
            valid.append(item)
        if not valid:self.status.set('No matching suggestions at this position.');return
        if self.popup is None:
            self.popup=tk.Toplevel(self.root);self.popup.withdraw();self.popup.overrideredirect(True);self.popup.transient(self.root)
            frame=ttk.Frame(self.popup,padding=4);frame.pack(fill='both',expand=True)
            self.listing=tk.Listbox(frame,height=9,width=48,font=self.editor.ui_font,exportselection=False,
                background='#11161c',foreground='#d1dae4',selectbackground='#1597f5',selectforeground='white',takefocus=False)
            self.listing.pack(side='left',fill='both',expand=True)
            scroll=ttk.Scrollbar(frame,command=self.listing.yview);scroll.pack(side='right',fill='y');self.listing.configure(yscrollcommand=scroll.set)
            def click(event):
                self.listing.selection_clear(0,'end');self.listing.selection_set(self.listing.nearest(event.y));self.accept();return 'break'
            self.listing.bind('<ButtonRelease-1>',click)
        self.items=valid;self.completion_capture=capture;self.listing.delete(0,'end')
        for item in valid:self.listing.insert('end',item['label']+('  · '+str(item['detail'])[:55] if item.get('detail') else ''))
        self.listing.selection_set(0);box=self.text.bbox('insert')
        if not box:self.dismiss();return
        self.popup.update_idletasks();width=self.popup.winfo_reqwidth();height=self.popup.winfo_reqheight()
        x=max(0,min(self.text.winfo_rootx()+box[0],self.root.winfo_screenwidth()-width))
        y=self.text.winfo_rooty()+box[1]+box[3]
        if y+height>self.root.winfo_screenheight():y=max(0,y-height-box[3])
        self.popup.geometry(f'+{x}+{y}');self.popup.deiconify();self.popup.lift()
        self.status.set(f'{len(valid)} suggestions · Enter/Tab accepts · Escape closes')

    def move(self,delta):
        if not self.items:return
        selected=self.listing.curselection();i=max(0,min(len(self.items)-1,(selected[0] if selected else 0)+delta))
        self.listing.selection_clear(0,'end');self.listing.selection_set(i);self.listing.see(i);return 'break'

    def clear_stops(self):
        for widget,start,end in self.stops:
            if widget.winfo_exists():widget.mark_unset(start,end)
        self.stops=[];self.stop_number=-1

    def select_stop(self,number):
        if not 0<=number<len(self.stops):self.clear_stops();return
        widget,start,end=self.stops[number]
        if widget is not self.text.widget:self.clear_stops();return
        self.stop_number=number;self.text.tag_remove('sel','1.0','end');self.text.tag_add('sel',start,end)
        self.text.mark_set('insert',start);self.text.see('insert')

    def accept(self,event=None):
        if not self.items:return
        capture=self.completion_capture
        if not self.valid(capture,True):self.dismiss();return 'break'
        selected=self.listing.curselection();item=self.items[selected[0] if selected else 0]
        try:edits,caret,stops=prepare_completion(capture[2],capture[3],item)
        except (KeyError,ValueError,TypeError) as error:self.status.set(str(error));self.dismiss();return 'break'
        self.dismiss();self.clear_stops();self.text.edit_separator()
        for start,end,value,_primary in reversed(edits):self.text.replace(index(capture[2],start),index(capture[2],end),value)
        self.text.edit_separator();source=self.api.get_text();self.text.tag_remove('sel','1.0','end')
        self.text.mark_set('insert',index(source,caret))
        for i,(_number,start,end) in enumerate(stops[:20]):
            a,b=f'web_stop_{i}_start',f'web_stop_{i}_end';self.text.mark_set(a,index(source,start));self.text.mark_set(b,index(source,end))
            self.text.mark_gravity(a,'left');self.text.mark_gravity(b,'right');self.stops.append((self.text.widget,a,b))
        if self.stops:self.select_stop(0)
        self.text.focus_set();self.editor.refresh();return 'break'

    def tab(self,event=None):
        if self.items:return self.accept(event)
        if self.stops:self.select_stop(self.stop_number+1);return 'break'

    def back_tab(self,event=None):
        if self.stops:self.select_stop(max(0,self.stop_number-1));return 'break'

    def jump(self,issue):
        if self.checked and self.valid(self.checked):
            self.text.mark_set('insert',index(self.checked[2],issue['start']));self.text.see('insert');self.text.focus_set()

    def mount(self,parent):
        if self.panel is not None and self.panel.winfo_exists():return self.panel
        outer=self.panel=ttk.Frame(parent);outer.pack(fill='both',expand=True)
        canvas=tk.Canvas(outer,width=260,highlightthickness=0,background='#11161c');scroll=ttk.Scrollbar(outer,command=canvas.yview)
        scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True);canvas.configure(yscrollcommand=scroll.set)
        body=ttk.Frame(canvas,padding=12);item=canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
        ttk.Label(body,text='WEB DEVELOPMENT',style='Title.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Label(body,text='HTML · CSS · JavaScript\nJSX / TypeScript · SCSS / LESS',wraplength=260).pack(anchor='w',pady=(0,8))
        for label,callback in (('Format document  ·  Ctrl+Shift+F',self.format_document),('Autocomplete  ·  Ctrl+Space',self.complete),
                ('Check syntax',lambda:self.request('check')),('Install / Repair Tools',lambda:self.request('install'))):
            ttk.Button(body,text=label,command=callback).pack(fill='x',pady=3)
        self.variables={key:tk.BooleanVar(self.root,self.settings[key]) for key in ('automatic','tabs','singleQuote','semi')}
        for key,label in (('automatic','Automatic completion'),('tabs','Indent with tabs'),('singleQuote','Prefer single quotes'),('semi','JavaScript semicolons')):
            ttk.Checkbutton(body,text=label,variable=self.variables[key]).pack(anchor='w',pady=3)
        for key,label,values in (('indent','Indent size',(2,4)),('width','Print width',(80,100,120)),('delay','Completion delay (ms)',(150,250,400,600))):
            ttk.Label(body,text=label).pack(anchor='w',pady=(8,2));self.variables[key]=tk.StringVar(self.root,str(self.settings[key]))
            ttk.Combobox(body,textvariable=self.variables[key],values=values,state='readonly',width=12).pack(fill='x')
        ttk.Button(body,text='Save settings',command=self.save_settings).pack(fill='x',pady=10)
        ttk.Label(body,textvariable=self.status,wraplength=250).pack(fill='x',pady=8)
        self.issue_list=tk.Listbox(body,height=6,exportselection=False,background='#11161c',foreground='#d1dae4')
        self.issue_list.pack(fill='x')
        for issue in self.issues:self.issue_list.insert('end',issue['message'])
        self.issue_list.bind('<<ListboxSelect>>',lambda event:self.jump(self.issues[self.issue_list.curselection()[0]]) if self.issue_list.curselection() else None)
        ttk.Label(body,text='Install Node.js LTS first. Tool setup downloads pinned packages once. Operations then run locally. Format is undoable; Save writes files.',wraplength=250).pack(fill='x',pady=10)
        def wheel(event):canvas.yview_scroll(-1 if getattr(event,'num',0)==4 or getattr(event,'delta',0)>0 else 1,'units');return 'break'
        def bind(widget):
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):widget.bind(sequence,wheel,add='+')
            for child in widget.winfo_children():bind(child)
        bind(body);return outer

    def show(self):self.editor.plugin_sidebar.open(self.api.plugin_id)

    def save_settings(self):
        settings={key:variable.get() for key,variable in self.variables.items()}
        for key in ('indent','width','delay'):settings[key]=int(settings[key])
        self.editor.write_data(self.settings_path,(json.dumps(settings,indent=2)+'\n').encode('utf-8'))
        self.settings=read_settings(self.settings_path);self.status.set('Web Tools settings saved.')

    def poll(self):
        if self.closed:return
        for future,job in list(self.jobs.items()):
            if not future.done():continue
            kind,capture,token,manual=job;self.jobs.pop(future)
            try:
                value=future.result()
                if future in self.cancelled:self.cancelled.discard(future);continue
                if kind=='install':self.status.set(value);continue
                if not self.valid(capture,kind=='complete'):
                    if manual:self.status.set('Document changed; stale result discarded. Run the action again.')
                    elif kind=='complete' and self.settings['automatic'] and language(self.api.get_path()):
                        self.cancel_timer()
                        def retry():self.pending=None;self.request('complete',False)
                        self.pending=self.root.after(self.settings['delay'],retry)
                    continue
                if kind=='format':
                    formatted=value['formatted'];units=value['cursorOffset']
                    caret=len(formatted.encode('utf-16-le')[:max(0,units)*2].decode('utf-16-le'))
                    if formatted!=capture[2]:self.clear_stops();self.api.replace_text(formatted)
                    self.text.mark_set('insert',index(formatted,caret));self.text.see('insert');self.editor.refresh()
                    self.status.set('Formatted. Ctrl+Z undoes; Save writes the file.')
                elif kind=='complete':self.suggestions(value,capture)
                else:
                    self.issues=[]
                    for issue in value:
                        start=offset_at(capture[2],issue['range']['start'])
                        try:end=offset_at(capture[2],issue['range']['end'])
                        except ValueError:end=min(len(capture[2]),start+1)
                        self.issues.append(dict(start=start,end=end,message=issue['message'],severity='error' if issue.get('severity')==1 else 'warning'))
                    self.checked=capture;self.marks.show(capture[2],self.issues,lambda capture=capture:self.valid(capture))
                    self.status.set(f'{len(self.issues)} syntax issues.' if self.issues else 'No syntax issues found by the installed tools.')
                    if self.panel is not None:
                        self.issue_list.delete(0,'end')
                        for issue in self.issues:self.issue_list.insert('end',issue['message'])
            except Exception as error:
                if kind=='install' or self.valid(capture):self.status.set(str(error)[:500])
            finally:_loading.end(self.api,token,ready=lambda:self.closed or self.api.plugin_id not in self.editor.plugin_sidebar.pending)
        if self.checked:self.marks.draw()
        self.poll_job=self.root.after(60,self.poll)

    def cleanup(self):
        if self.closed:return
        self.closed=True;self.cancel_timer();self.root.after_cancel(self.poll_job)
        self.worker.close();self.executor.shutdown(wait=False,cancel_futures=True)
        for _future,(_kind,_capture,token,_manual) in self.jobs.items():_loading.cancel(self.api,token)
        self.jobs.clear();self.clear_stops();self.marks.clear()
        for sequence,binding in self.bindings:self.text.unbind(sequence,binding)
        for window in (self.popup,self.panel):
            if window is not None and window.winfo_exists():window.destroy()


def register(api):WebTools(api)
