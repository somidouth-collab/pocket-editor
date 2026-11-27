"""Background checks for saved documents; all widget work stays on Tk's thread."""
from pathlib import Path
import codecs
import hashlib
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import messagebox,ttk
from file_probe import AUTO_LIMIT,fingerprint,inspect_file



class ProbeJob:
    """A cancellable subprocess; no background threads own or collect Tk objects."""
    def __init__(self,records):
        self.records=records;self.directory=tempfile.TemporaryDirectory(prefix='pocket-file-check-')
        self.process=None;self.error=None;self.output=Path(self.directory.name)/'result.json'
        try:
            source=Path(self.directory.name)/'request.json';source.write_text(json.dumps(records),encoding='utf-8')
            self.process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('file_probe.py')),str(source),str(self.output)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
        except OSError as error:self.error=str(error)

    def done(self):return self.error is not None or self.process.poll() is not None

    def result(self):
        try:
            if self.error is not None:raise OSError(self.error)
            if self.process.returncode:raise OSError('File-change checker could not complete. Checks will retry.')
            results=json.loads(self.output.read_text(encoding='utf-8'))
            for value in results:
                if value[3] is not None:value[3]=tuple(value[3])
                if value[2]=='content' and value[-1] is not None:value[-1]=base64.b64decode(value[-1])
            return results
        except (OSError,ValueError) as error:
            return [(record[0],record[1],'error',None,None,str(error)) for record in self.records]
        finally:self.directory.cleanup()

    def cancel(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:pass
        self.directory.cleanup()


class ExternalFiles:
    def __init__(self,editor,parent):
        self.editor,self.root=editor,editor.root;self.states={};self.serial=0;self.closed=False
        self.future=None;self.next_check=time.monotonic()+1;self.offset=0
        self.banner=ttk.Frame(parent,padding=(10,6));self.message=tk.StringVar(self.root)
        self.label=ttk.Label(self.banner,textvariable=self.message,wraplength=500)
        self.label.pack(side='left',fill='x',expand=True)
        self.reload_button=ttk.Button(self.banner,text='Reload',command=self.reload)
        self.reload_button.pack(side='right',padx=3)
        self.keep_button=ttk.Button(self.banner,text='Keep Mine',command=self.keep)
        self.keep_button.pack(side='right',padx=3)
        ttk.Button(self.banner,text='×',width=2,command=self.dismiss).pack(side='right',padx=3)
        self.banner.bind('<Configure>',lambda event:self.label.configure(wraplength=max(100,event.width-240)))
        self.job=self.root.after(200,self.tick)
        self.destroy_binding=self.root.bind('<Destroy>',lambda event:self.close() if event.widget is self.root else None,add='+')

    def track(self,document,data=None,digest=None):
        path=document.get('path')
        if path is None:return
        key=str(document['widget']);self.serial+=1
        if data is not None:digest=hashlib.sha256(data).hexdigest()
        if digest is not None:document['disk_digest']=digest
        state=dict(document=document,path=Path(path).absolute(),serial=self.serial,digest=digest,
                   observed=digest,signature=None,candidate=None,verified=0,notice=None,force=False,manual=None,missing=False,problem=None,unverified=False)
        self.states[key]=state
        self.next_check=min(self.next_check,time.monotonic())
        self.render()
        return state

    def current(self):return self.states.get(str(self.editor.buffer.widget))

    def dirty(self,document):
        widget=document['widget']
        return bool(widget.edit_modified() or document.get('rich_dirty') or
                    (document is self.editor.active_document and self.editor.rich_dirty) or
                    any(widget.tag_ranges(tag) for tag in widget.tag_names() if tag.startswith('rich_')))

    def baseline(self,state,digest,signature):
        state.update(digest=digest,observed=digest,signature=signature,candidate=signature,
                     verified=time.monotonic(),missing=False,problem=None,unverified=False)
        state['document']['disk_digest']=digest

    def collect(self):
        editor=self.editor;editor.remember_document();live={}
        for doc in editor.documents:
            if doc['path'] is None:continue
            key=str(doc['widget']);live[key]=doc
            state=self.states.get(key)
            if state is None or state['path']!=Path(doc['path']).absolute():
                self.track(doc,digest=doc.get('disk_digest'))
        for key in list(self.states):
            if key not in live:self.states.pop(key)
        states=list(self.states.items())
        if not states:return []
        start=self.offset%len(states);states=states[start:]+states[:start];self.offset=start+16
        return [(key,s['serial'],str(s['path']),s['signature'],s['candidate'],s['observed'],
                 time.monotonic()-s['verified']>=30,s['force']) for key,s in states[:16]]

    def modal_open(self):
        try:return bool(self.root.tk.call('grab','current',self.root._w))
        except tk.TclError:return True

    def tick(self):
        if self.closed:return
        if self.future is not None and self.future.done():
            try:
                for result in self.future.result():self.apply(result)
            finally:self.future=None
        if self.closed:return
        if self.future is None and time.monotonic()>=self.next_check:
            records=[] if self.modal_open() else self.collect()
            if records:self.future=ProbeJob(records)
            self.next_check=time.monotonic()+2
        self.render();self.job=self.root.after(200,self.tick)

    def apply(self,result):
        key,serial,kind,signature,digest,data=result;state=self.states.get(key)
        if state is None or state['serial']!=serial:return
        doc=state['document']
        if not any(item is doc for item in self.editor.documents):return
        if kind=='unchanged':return
        if kind=='settling':
            state['candidate']=signature;self.next_check=min(self.next_check,time.monotonic()+.25);return
        if kind=='same':
            state.update(signature=signature,candidate=signature,verified=time.monotonic(),missing=False,problem=None,unverified=False)
            if digest==state['digest'] and state['notice'] and state['notice'][0] in ('missing','error'):state['notice']=None
            return
        if kind=='content' and self.modal_open():
            self.next_check=min(self.next_check,time.monotonic()+.25);return
        manual=state['manual'];state['force']=False;state['manual']=None
        if kind in ('missing','error','large'):
            problem=(kind,signature,str(data) if kind=='error' else None)
            if state['problem']==problem:return
            state['problem']=problem;state['unverified']=True
            if kind=='large' and digest is not None:
                if state['digest'] is None:self.baseline(state,digest,signature);return
                state['observed']=digest
            # Do not turn an unsaved, newly named buffer into an external-delete alert.
            if state['digest'] is None and state['signature'] is None and kind=='missing':return
            if kind=='missing':
                state['missing']=True;doc['widget'].edit_modified(True)
                text='File was removed or renamed outside the editor. Your open copy is preserved.'
            elif kind=='large':text='Large file changed outside the editor. Reload it explicitly to refresh.'
            else:
                doc['widget'].edit_modified(True)
                text='Cannot read the disk file. Your open copy is preserved. '+str(data)[:120]
            state['notice']=(kind,text);state['signature']=signature
            return
        if manual is not None and manual!=(self.editor.document_generation,self.editor.document_revision,doc['widget'].get('1.0','end-1c')):
            state['notice']=('changed','Document edited while reload was pending. Your edits were kept.');return
        state.update(signature=signature,candidate=signature,observed=digest,verified=time.monotonic(),missing=False,problem=None,unverified=False)
        if state['digest'] is None and manual is None:
            self.baseline(state,digest,signature);return
        if digest==state['digest'] and manual is None:
            state['notice']=None;return
        try:
            content=data.decode('utf-8-sig');newline='\r\n' if '\r\n' in content else '\r' if '\r' in content else '\n'
            content=content.replace('\r\n','\n').replace('\r','\n');bom=data.startswith(codecs.BOM_UTF8)
        except UnicodeError:
            doc['widget'].edit_modified(True)
            state['notice']=('error','Disk file is no longer valid UTF-8. Your open copy was kept.');return
        if manual is None and self.dirty(doc):
            state['notice']=('changed','File changed outside the editor. Unsaved edits were kept.');return
        widget=doc['widget'];cursor=widget.index('insert');view=widget.yview();selection=widget.tag_ranges('sel')
        widget.edit_separator();widget.replace('1.0','end-1c',content);widget.edit_separator()
        for tag in widget.tag_names():
            if tag.startswith('rich_'):widget.tag_remove(tag,'1.0','end')
        widget.mark_set('insert',cursor)
        if view:widget.yview_moveto(view[0])
        if selection:widget.tag_add('sel',*selection)
        widget.edit_modified(False);doc.update(newline=newline,bom=bom,rich_dirty=False)
        if doc is self.editor.active_document:
            self.editor.newline,self.editor.bom,self.editor.rich_dirty=newline,bom,False
        self.baseline(state,digest,signature)
        state['notice']=('reloaded','Reloaded: file changed outside the editor. Undo can recover the previous text.')
        self.editor.refresh();self.editor.dispatch_plugin_event('document_reloaded','editor',{'path':str(state['path'])})

    def reload(self):
        state=self.current()
        if state is None:return
        doc=state['document']
        if self.dirty(doc) and not messagebox.askyesno('Reload from disk','Replace unsaved edits with the latest disk version? You can Undo the reload.',parent=self.root):return
        self.serial+=1;state['serial']=self.serial;state['force']=True
        state['manual']=(self.editor.document_generation,self.editor.document_revision,doc['widget'].get('1.0','end-1c'))
        state['notice']=('loading','Reading the latest disk version…');self.next_check=time.monotonic();self.render()

    def keep(self):
        state=self.current()
        if state is None:return
        problem,missing=state['problem'],state['missing']
        self.baseline(state,state['observed'],state['signature'])
        state['problem'],state['missing']=problem,missing
        state['document']['widget'].edit_modified(True);state['notice']=None
        self.editor.refresh();self.render()

    def dismiss(self):
        state=self.current()
        if state:state['notice']=None
        self.render()

    def render(self):
        if self.closed:return
        state=self.current();notice=state['notice'] if state else None
        for doc in self.editor.documents:
            watched=self.states.get(str(doc['widget']))
            marker=bool(watched and watched['notice'] and watched['notice'][0]!='loading')
            if doc.get('external_changed')!=marker:
                doc['external_changed']=marker;self.editor.refresh_tabs()
        if not notice:self.banner.pack_forget();return
        kind,text=notice;self.message.set(state['path'].name+' — '+text)
        self.reload_button.configure(state='disabled' if kind in ('missing','loading') else 'normal')
        if kind in ('reloaded','loading'):self.keep_button.pack_forget()
        else:self.keep_button.pack(side='right',padx=3)
        if not self.banner.winfo_manager():self.banner.pack(fill='x',before=self.editor.editor_panel)

    def before_save(self,path):
        state=self.current()
        if state is None or state['path']!=Path(path).absolute() or state['digest'] is None:return True
        try:
            digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if digest==state['digest']:return True
            text='This file changed outside the editor. Overwrite the disk version with your open copy?'
        except FileNotFoundError:
            text='This file was removed outside the editor. Recreate it with your open copy?'
        except OSError as error:
            messagebox.showerror('Cannot verify disk file',str(error),parent=self.root);return False
        return messagebox.askyesno('External file change',text,parent=self.root)

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.root.after_cancel(self.job)
        except tk.TclError:pass
        try:self.root.unbind('<Destroy>',self.destroy_binding)
        except tk.TclError:pass
        if self.future:self.future.cancel()
