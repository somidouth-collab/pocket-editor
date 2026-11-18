"""PHP project completion: responsive LSP scheduling and a keyboard-friendly Tk UI."""
from pathlib import Path
import json
import queue
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pocket_editor_plugins._php_tools import is_php, project_root
from pocket_editor_plugins._php_completion import (
    Capture, CompletionWorker, VERSIONS, read_settings, server_config, ranked_items, prepare_completion)

KINDS = {1:'Text', 2:'Method', 3:'Function', 4:'Constructor', 5:'Field', 6:'Variable',
         7:'Class', 8:'Interface', 9:'Namespace', 10:'Property', 12:'Value', 13:'Enum',
         14:'Keyword', 15:'Snippet', 20:'Enum case', 21:'Constant', 22:'Struct', 25:'Type'}


class PHPCompletion:
    def __init__(self, api):
        self.api, self.editor = api, api._editor
        self.root, self.text = self.editor.root, self.editor.buffer
        self.settings_path = Path(__file__).with_name('php_completion_settings.json')
        self.settings = read_settings(self.settings_path)
        self.worker = CompletionWorker()
        self.closed = False
        self.pending = self.warming = self.detail_pending = None
        self.detail_cache = {}
        self.detail_request_key = None
        self.popup = self.listing = self.description = self.footer = None
        self.window = None
        self.shown = []
        self.capture = None
        self.ticket = None
        self.directory = None
        self.manual_request = False
        self.busy = False
        self.error = ''
        self.items = ()
        self.bindings = []
        self.snippet_stops = []
        self.snippet_index = -1
        self.snippet_document = None
        self.last_document = None
        self.key_revisions = {}
        for sequence, callback in (
            ('<Control-space>', self.manual), ('<KeyPress>', self.key_pressed),
            ('<KeyRelease>', self.key_released),
            ('<Return>', self.accept), ('<Tab>', self.tab), ('<Shift-Tab>', self.back_tab),
            ('<ISO_Left_Tab>', self.back_tab), ('<Up>', lambda event: self.navigate(-1)),
            ('<Down>', lambda event: self.navigate(1)), ('<Prior>', lambda event: self.navigate(-8)),
            ('<Next>', lambda event: self.navigate(8)), ('<Escape>', self.escape),
            ('<ButtonRelease-1>', lambda event: self.dismiss()),
            ('<FocusOut>', lambda event: self.dismiss()),
            ('<MouseWheel>', lambda event: self.dismiss()),
            ('<Button-4>', lambda event: self.dismiss()), ('<Button-5>', lambda event: self.dismiss())):
            self.bindings.append((sequence, self.text.bind(sequence, callback, add='+')))
        api.on('document_changed', self.changed)
        api.on('document_saved', self.saved)
        api.on('project_changed', lambda **event: self.worker.invalidate(reindex=True))
        api.on_cleanup(self.cleanup)
        api.add_command('PHP Completion', self.manual)
        api.add_command('PHP Completion Settings', self.preferences)
        api.add_command('PHP Completion Help', self.help)
        api.add_command('Reindex PHP Project', self.reindex)
        api.provide_service('complete', self.manual)
        api.provide_service('status', self.status)
        self.root.after(20, self.poll)
        self.warming = self.root.after(250, self.warm)

    def identity(self):
        return self.api.get_path(), self.editor.document_generation, self.editor.document_revision, self.text.index('insert')

    def capture_now(self):
        return Capture(self.api.get_path(), self.api.get_text(), self.editor.document_generation,
                       self.editor.document_revision, self.text.index('insert'), len(self.text.get('1.0','insert')))

    def workspace(self):
        try:
            selected = self.api.call_service('project_explorer.get_project')
        except LookupError:
            selected = None
        return project_root(self.api.get_path(), selected)

    def hide(self):
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.withdraw()

    def dismiss(self, cancel=True):
        self.hide()
        self.cancel_detail()
        if self.pending is not None:
            self.root.after_cancel(self.pending); self.pending = None
        if cancel:
            self.worker.cancel(); self.ticket = None; self.busy = False

    def ensure_popup(self):
        if self.popup is not None:
            return
        self.popup = tk.Toplevel(self.root)
        self.popup.withdraw(); self.popup.overrideredirect(True); self.popup.transient(self.root)
        frame = ttk.Frame(self.popup, padding=1); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='PHP COMPLETION', style='Caption.TLabel', padding=(9,6)).pack(fill='x')
        body = ttk.Frame(frame); body.pack(fill='both', expand=True)
        self.listing = tk.Listbox(body, font=self.editor.ui_font, width=54, height=9, borderwidth=0,
            highlightthickness=1, highlightbackground='#52617e', background='#202633',
            foreground='#d8deed', selectbackground='#34456c', selectforeground='#ffffff',
            exportselection=False, activestyle='none', takefocus=False)
        self.listing.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, command=self.listing.yview)
        scroll.pack(side='right', fill='y'); self.listing.configure(yscrollcommand=scroll.set)
        self.description = ttk.Label(frame, style='Muted.TLabel', padding=(9,7), wraplength=460)
        self.description.pack(fill='x')
        self.footer = ttk.Label(frame, style='Caption.TLabel', padding=(9,5))
        self.footer.pack(fill='x')
        self.listing.bind('<ButtonPress-1>', self.clicked)
        self.listing.bind('<<ListboxSelect>>', lambda event: self.describe())

    def clicked(self, event):
        index = self.listing.nearest(event.y)
        self.listing.selection_clear(0,'end'); self.listing.selection_set(index)
        self.accept()
        return 'break'

    def cancel_detail(self):
        if self.detail_pending is not None:
            self.root.after_cancel(self.detail_pending); self.detail_pending = None

    def detail_key(self, item):
        return self.capture.identity, item['label'], str(item.get('data'))

    def describe(self):
        self.cancel_detail()
        selected = self.listing.curselection()
        if not selected or selected[0] >= len(self.shown):
            return
        item = self.shown[selected[0]]
        key = self.detail_key(item)
        item = self.detail_cache.get(key, item)
        kind = KINDS.get(item.get('kind'), 'Symbol')
        detail = item.get('detail', '')
        documentation = item.get('documentation', '')
        if isinstance(documentation, dict):
            documentation = documentation.get('value', '')
        description = kind + (' · ' + detail if detail else '')
        if documentation:
            description += '\n' + documentation.replace('```php','').replace('```','').replace('**','').strip()[:320]
        self.description.configure(text=description[:500])
        current, ticket = self.worker.server, self.ticket
        if key not in self.detail_cache and current is not None and current.can_resolve:
            def load_detail():
                self.detail_pending = None
                if self.closed or self.busy or ticket != self.ticket or self.capture.identity != self.identity():
                    return
                if self.popup.winfo_viewable():
                    self.detail_request_key = key
                    self.ticket = self.worker.submit('detail',self.capture,self.directory,
                        server_config(self.settings,self.directory),item)
            self.detail_pending = self.root.after(120,load_detail)

    def show(self, items, capture):
        if not items:
            self.hide()
            if self.manual_request:
                self.editor.status.configure(text='PHP completion · No matching suggestions. Check PHP Completion Help for project setup.')
            return
        self.ensure_popup()
        old = self.listing.get(self.listing.curselection()[0]) if self.listing.curselection() else None
        self.shown, self.capture = items, capture
        self.listing.delete(0,'end')
        for item in items:
            self.listing.insert('end',item['label'])
        labels = [item['label'] for item in items]
        index = labels.index(old) if old in labels else 0
        self.listing.configure(height=min(10,max(3,len(items))))
        self.listing.selection_set(index); self.listing.activate(index); self.listing.see(index)
        self.describe()
        latency = self.worker.status()['last_latency_ms']
        self.footer.configure(text=f'{len(items)} {"suggestion" if len(items)==1 else "suggestions"} · {latency:g} ms · Enter / Tab to insert · Esc to close')
        self.position_popup()

    def position_popup(self):
        bounds = self.text.bbox('insert')
        if not bounds:
            return
        x,y,_,height = bounds
        self.popup.update_idletasks()
        px = min(self.text.winfo_rootx()+x, self.root.winfo_screenwidth()-self.popup.winfo_reqwidth()-8)
        py = self.text.winfo_rooty()+y+height+4
        if py+self.popup.winfo_reqheight() > self.root.winfo_screenheight():
            py = self.text.winfo_rooty()+y-self.popup.winfo_reqheight()-4
        self.popup.geometry(f'+{max(0,px)}+{max(0,py)}')
        self.popup.deiconify(); self.popup.lift()

    def warm(self):
        self.warming = None
        if self.closed or self.editor.closing or self.busy or not is_php(self.api.get_path()) or (self.popup is not None and self.popup.winfo_viewable()):
            return
        capture = self.capture_now()
        directory = self.workspace()
        self.worker.submit('warm',capture,directory,server_config(self.settings,directory))

    def request(self, manual=False):
        self.cancel_detail()
        if not is_php(self.api.get_path()):
            self.dismiss(); return
        if self.pending is not None:
            self.root.after_cancel(self.pending); self.pending = None
        self.directory = self.workspace()
        self.capture = self.capture_now()
        self.manual_request, self.busy, self.error, self.items = manual, True, '', ()
        self.ticket = self.worker.submit('complete', self.capture, self.directory,
                                         server_config(self.settings,self.directory))
        if manual:
            self.editor.status.configure(text='PHP completion · Collecting suggestions…')

    def poll(self):
        if self.closed or self.editor.closing:
            return
        current = self.worker.server
        if current is not None:
            try:
                for _ in range(8):
                    notice = current.notices.get_nowait()
                    display = {1:messagebox.showerror,2:messagebox.showwarning}.get(notice.get('type'),messagebox.showinfo)
                    display('PHP language server',notice.get('message',''),parent=self.root)
            except queue.Empty:
                pass
        try:
            while True:
                ticket,kind,capture,value,error = self.worker.results.get_nowait()
                if ticket != self.ticket:
                    continue
                self.busy = False
                if capture.identity != self.identity() or not is_php(self.api.get_path()):
                    self.hide(); self.items = (); continue
                self.error = error
                self.editor.refresh()
                if error and kind == 'detail':
                    if self.footer is not None:
                        self.footer.configure(text='Details unavailable · Enter / Tab to insert')
                elif error:
                    self.hide()
                    if self.manual_request:
                        messagebox.showerror('PHP completion unavailable',error,parent=self.root)
                elif kind == 'detail':
                    self.detail_cache[self.detail_request_key] = value
                    if len(self.detail_cache) > 50:
                        self.detail_cache.pop(next(iter(self.detail_cache)))
                    self.describe()
                    self.position_popup()
                elif kind == 'resolve':
                    self.apply(value)
                elif kind == 'complete':
                    valid = ranked_items(value,capture.source,capture.cursor,self.settings['max_items'])
                    self.items = tuple(item['label'] for item in valid)
                    self.show(valid,capture)
        except queue.Empty:
            pass
        self.root.after(20,self.poll)

    def accept(self,event=None):
        if self.popup is None or not self.popup.winfo_viewable() or not self.shown:
            return None
        selected = self.listing.curselection()
        if not selected:
            return 'break'
        if self.capture.identity != self.identity():
            self.dismiss(); return 'break'
        self.cancel_detail()
        item = self.shown[selected[0]]
        item = self.detail_cache.get(self.detail_key(item), item)
        current = self.worker.server
        if current is not None and current.can_resolve and self.detail_key(item) not in self.detail_cache:
            self.busy = True
            self.ticket = self.worker.submit('resolve',self.capture,self.directory,
                                             server_config(self.settings,self.directory),item)
            self.footer.configure(text='Completing…')
        else:
            self.apply(item)
        return 'break'

    def apply(self,item):
        if self.capture is None or self.capture.identity != self.identity():
            self.dismiss(); return
        try:
            edits,caret,stops = prepare_completion(self.capture.source,self.capture.cursor,item)
        except (KeyError,TypeError,ValueError) as error:
            self.error = str(error)
            if self.footer is not None:
                self.footer.configure(text=self.error)
            return
        self.clear_snippet()
        self.text.edit_separator()
        for start,end,value,primary in reversed(edits):
            self.text.replace(f'1.0 +{start}c',f'1.0 +{end}c',value)
        self.text.edit_separator()
        self.text.mark_set('insert',f'1.0 +{caret}c')
        self.hide(); self.text.focus_set(); self.editor.refresh()
        self.snippet_document = (self.api.get_path(), self.editor.document_generation)
        for index,(number,start,end) in enumerate(stops):
            begin,finish = f'php_arg_{index}_start',f'php_arg_{index}_end'
            self.text.mark_set(begin,f'1.0 +{start}c'); self.text.mark_gravity(begin,'left')
            self.text.mark_set(finish,f'1.0 +{end}c'); self.text.mark_gravity(finish,'right')
            self.snippet_stops.append((number,begin,finish))
        self.snippet_index = -1
        if self.snippet_stops:
            self.advance_snippet(1)
        self.text.see('insert')

    def clear_snippet(self):
        for _,begin,end in self.snippet_stops:
            self.text.mark_unset(begin,end)
        self.snippet_stops = []; self.snippet_index = -1; self.snippet_document = None

    def advance_snippet(self,direction):
        if self.snippet_document != (self.api.get_path(),self.editor.document_generation):
            self.clear_snippet(); return None
        if not self.snippet_stops:
            return None
        index = self.snippet_index + direction
        if index < 0:
            index = 0
        if index >= len(self.snippet_stops):
            self.clear_snippet(); return 'break'
        self.snippet_index = index
        number,begin,end = self.snippet_stops[index]
        self.text.tag_remove('sel','1.0','end'); self.text.mark_set('insert',begin)
        self.text.tag_add('sel',begin,end); self.text.see('insert')
        if number == 0:
            self.clear_snippet()
        return 'break'

    def tab(self,event=None):
        return self.accept(event) or self.advance_snippet(1)

    def back_tab(self,event=None):
        return self.advance_snippet(-1)

    def navigate(self,direction):
        if self.popup is None or not self.popup.winfo_viewable() or not self.shown:
            return None
        selected = self.listing.curselection()
        index = ((selected[0] if selected else 0)+direction) % len(self.shown)
        self.listing.selection_clear(0,'end'); self.listing.selection_set(index)
        self.listing.activate(index); self.listing.see(index); self.describe()
        return 'break'

    def manual(self,event=None):
        if not is_php(self.api.get_path()):
            return None
        self.request(True); return 'break'

    def escape(self,event=None):
        active = (self.popup is not None and self.popup.winfo_viewable()) or self.busy or bool(self.snippet_stops)
        self.dismiss(); self.clear_snippet()
        return 'break' if active else None

    def key_pressed(self,event):
        # Widget bindings run before Text's class binding performs the edit.
        # KeyRelease.char is empty in Tk; compare revisions across the key pair.
        self.key_revisions[event.keysym] = self.editor.document_revision

    def key_released(self,event):
        before = self.key_revisions.pop(event.keysym, None)
        edited = before is not None and before != self.editor.document_revision
        if event.keysym in ('Up','Down','Return','Tab','ISO_Left_Tab','Prior','Next','Escape',
                            'Control_L','Control_R','Shift_L','Shift_R','Alt_L','Alt_R','Meta_L','Meta_R'):
            return
        if not edited:
            if event.keysym in ('Left','Right','Home','End'):
                self.dismiss()
            # Releasing Ctrl+Space must not cancel the manual request it started.
            return
        self.dismiss()
        if not self.settings['automatic'] or not is_php(self.api.get_path()):
            return
        prefix = self.text.get('insert linestart','insert')
        if prefix.lstrip().startswith(('//','#')) and not prefix.lstrip().startswith('#['):
            return
        if re.search(r'(?:[$\w\\]{'+str(self.settings['min_chars'])+r',}|->\w*|::\w*|@\w*)$',prefix):
            def delayed():
                self.pending = None; self.request()
            self.pending = self.root.after(self.settings['delay_ms'],delayed)

    def changed(self,**event):
        document = (self.api.get_path(),self.editor.document_generation)
        if document != self.last_document:
            self.last_document = document
            self.clear_snippet()
            if self.warming is not None:
                self.root.after_cancel(self.warming)
            self.warming = self.root.after(250,self.warm)
        if self.capture is not None and self.capture.identity != self.identity():
            # Keep a typing debounce already scheduled by KeyRelease.
            self.hide(); self.worker.cancel(); self.ticket = None; self.busy = False

    def saved(self,path,**event):
        if is_php(path):
            self.detail_cache.clear()
            self.worker.invalidate(path)

    def reindex(self):
        self.worker.invalidate(reindex=True); self.request(True)

    def help(self):
        state = self.status()
        messagebox.showinfo('PHP Completion',
            'Ctrl+Space: suggestions · Enter/Tab: insert · Esc: cancel\n'
            'Tab / Shift+Tab: move between inserted parameters.\n\n'
            'Project classes, use aliases, functions, constants, traits, enums,\n'
            'PHPDoc, standard PHP functions and Composer libraries are indexed.\n'
            'Add external libraries or framework stubs in PHP Completion Settings.\n\n'
            f"Workspace: {state['workspace'] or 'Open a PHP file'}\n"
            f"Phase: {state['phase']} · Last response: {state['last_latency_ms']:g} ms\n"
            + (self.error or 'Dynamic/magic members need type information or PHPDoc/stubs.'),parent=self.root)

    def preferences(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift(); return
        window = self.window = tk.Toplevel(self.root)
        window.title('PHP Completion Settings'); window.transient(self.root); window.resizable(False,False)
        body = ttk.Frame(window,padding=20); body.pack(fill='both',expand=True)
        automatic = tk.BooleanVar(window,self.settings['automatic'])
        delay = tk.StringVar(window,str(self.settings['delay_ms']))
        minimum = tk.StringVar(window,str(self.settings['min_chars']))
        maximum = tk.StringVar(window,str(self.settings['max_items']))
        ttk.Checkbutton(body,text='Show suggestions automatically in PHP files',variable=automatic).grid(row=0,column=0,columnspan=2,sticky='w')
        for row,(label,variable,low,high) in enumerate((('Typing delay (ms)',delay,50,1000),
                ('Minimum typed characters',minimum,1,4),('Maximum suggestions',maximum,20,500)),1):
            ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',pady=6,padx=(0,20))
            ttk.Spinbox(body,from_=low,to=high,textvariable=variable,width=8).grid(row=row,column=1,sticky='w')
        directory = self.workspace() if is_php(self.api.get_path()) else None
        project = self.settings['projects'].get(str(directory),{})
        version = tk.StringVar(window,project.get('php_version','8.4'))
        group = ttk.LabelFrame(body,text='Current PHP project',padding=12)
        group.grid(row=4,column=0,columnspan=2,sticky='ew',pady=14)
        ttk.Label(group,text=str(directory) if directory else 'Open a PHP file to configure its project.',wraplength=460).pack(anchor='w')
        row = ttk.Frame(group); row.pack(fill='x',pady=10)
        ttk.Label(row,text='Target PHP version').pack(side='left')
        ttk.Combobox(row,values=VERSIONS,textvariable=version,state='readonly' if directory else 'disabled',width=8).pack(side='right')
        ttk.Label(group,text='Extra library / framework stub folders').pack(anchor='w')
        paths = tk.Listbox(group,height=4,width=58,font=self.editor.ui_font,background='#171d29',foreground='#d8deed',
                           selectbackground='#34456c',exportselection=False)
        paths.pack(fill='x',pady=6)
        for value in project.get('include_paths',[]): paths.insert('end',value)
        actions = ttk.Frame(group); actions.pack(fill='x')
        def add_path():
            selected = filedialog.askdirectory(parent=window,title='PHP library or framework stubs')
            if selected and selected not in paths.get(0,'end'): paths.insert('end',selected)
        def remove_path():
            for index in reversed(paths.curselection()): paths.delete(index)
        ttk.Button(actions,text='Add folder…',command=add_path,state='normal' if directory else 'disabled').pack(side='left')
        ttk.Button(actions,text='Remove',command=remove_path).pack(side='left',padx=6)
        ttk.Label(body,text='Dependencies must exist on disk. PHPDoc/stubs describe dynamic framework APIs.',
                  style='Muted.TLabel',wraplength=480).grid(row=5,column=0,columnspan=2,sticky='w')
        def save():
            try:
                values = int(delay.get()),int(minimum.get()),int(maximum.get())
                if not (50<=values[0]<=1000 and 1<=values[1]<=4 and 20<=values[2]<=500): raise ValueError
            except ValueError:
                messagebox.showerror('Completion settings','Delay: 50–1000 ms. Minimum characters: 1–4. Suggestions: 20–500.',parent=window); return
            updated = {**self.settings,'automatic':automatic.get(),'delay_ms':values[0],'min_chars':values[1],
                       'max_items':values[2],'projects':dict(self.settings['projects'])}
            if directory:
                includes = list(paths.get(0,'end'))
                if any(not (directory/Path(path)).is_dir() for path in includes):
                    messagebox.showerror('Library folders','Choose existing library folders.',parent=window); return
                updated['projects'][str(directory)] = {'php_version':version.get(),'include_paths':includes}
            try:
                self.editor.write_data(self.settings_path,(json.dumps(updated,indent=2)+'\n').encode('utf-8'))
            except OSError as error:
                messagebox.showerror('Completion settings',str(error),parent=window); return
            self.settings = updated; self.dismiss(); window.destroy(); self.warm()
        buttons = ttk.Frame(body); buttons.grid(row=6,column=0,columnspan=2,sticky='e',pady=(16,0))
        ttk.Button(buttons,text='Cancel',command=window.destroy).pack(side='left',padx=8)
        ttk.Button(buttons,text='Save Settings',command=save,style='Accent.TButton').pack(side='left')

    def status(self):
        return {**self.worker.status(),'busy':self.busy,'error':self.error,'items':self.items}

    def cleanup(self):
        self.closed = True
        self.dismiss(); self.clear_snippet(); self.worker.close()
        if self.warming is not None: self.root.after_cancel(self.warming)
        for sequence,identifier in self.bindings: self.text.unbind(sequence,identifier)
        if self.popup is not None and self.popup.winfo_exists(): self.popup.destroy()
        if self.window is not None and self.window.winfo_exists(): self.window.destroy()


def register(api):
    PHPCompletion(api)
