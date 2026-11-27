"""Interactive Tk terminal surface. Rendering and emulator updates stay on Tk's thread."""
import locale
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk,font
from pocket_editor_plugins._terminal_colors import classify_line
from pocket_editor_plugins._terminal_session import TOOLS,TerminalSession,engine,make_screen

NAMES=['black','red','green','brown','blue','magenta','cyan','white','brightblack','brightred','brightgreen','brightyellow','brightblue','brightmagenta','brightcyan','brightwhite']


class InteractiveTerminal:
    def __init__(self,parent,root,status,appearance,shell):
        self.root,self.status,self.appearance,self.shell=root,status,appearance,shell
        self.frame=ttk.Frame(parent);self.closed=False;self.session=None;self.screen=None;self.stream=None
        self.setup_process=None;self.setup_messages=queue.Queue();self.setup_busy=False
        self.text=tk.Text(self.frame,wrap='none',borderwidth=0,padx=6,pady=6,undo=False,exportselection=False)
        self.text._terminal_role='shell';self.text.pack(side='left',fill='both',expand=True)
        self.scroll=ttk.Scrollbar(self.frame,command=self.scroll_view);self.scroll.pack(side='right',fill='y')
        self.fixed=font.Font(root=root,family='monospace',size=11);self.bold=self.fixed.copy();self.bold.configure(weight='bold')
        self.styles={};self.directory=None;self.running=False;self.popup_menu=None
        self.selecting=False;self.menu_open=False;self.redraw_pending=False
        self.apply_appearance(appearance)
        self.text.bind('<KeyPress>',self.key)
        self.text.bind('<<Paste>>',lambda event:self.paste() or 'break')
        self.text.bind('<<Cut>>',lambda event:self.copy() or 'break')
        for sequence in ('<<Undo>>','<<Redo>>','<<Clear>>'):self.text.bind(sequence,lambda event:'break')
        self.text.bind('<<SelectAll>>',lambda event:self.select_all() or 'break')
        self.text.bind('<Button-2>',lambda event:'break')
        self.text.bind('<Configure>',self.resize)
        for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):self.text.bind(sequence,self.wheel)
        self.text.bind('<ButtonPress-1>',self.selection_start,add='+')
        self.text.bind('<ButtonRelease-1>',self.selection_end,add='+')
        self.text.bind('<Button-3>',lambda event:'break')
        self.text.bind('<B3-Motion>',lambda event:'break')
        self.text.bind('<ButtonRelease-3>',self.context)
        self.job=root.after(40,self.poll)

    def start(self,directory):
        if self.closed:return
        if self.session is not None:self.session.close()
        self.running=False;self.selecting=False;self.text.tag_remove('sel','1.0','end')
        self.directory=str(Path(directory).expanduser().resolve())
        try:
            pyte,_transport=engine();self.session=TerminalSession()
            self.screen=make_screen(pyte,80,24,self.send);self.stream=pyte.Stream(self.screen)
            encoding=locale.getpreferredencoding(False) if self.appearance['encoding']=='locale' else self.appearance['encoding']
            self.session.start(self.directory,self.shell(),encoding=encoding,force_color=self.appearance['force_color'])
            self.status.set('Starting interactive shell…');self.resize();self.draw();self.text.focus_set()
        except (RuntimeError,OSError,ValueError) as error:
            self.status.set(str(error));self.text.delete('1.0','end');self.text.insert('1.0',str(error)+'\n')

    def stop(self):
        if self.session is not None:self.session.close()
        self.running=False;self.status.set('Shell stopped. Terminal → New / Restart Shell starts a fresh session.')

    def send(self,value):
        if self.session is None or not self.running:return False
        try:
            if not self.session.write(value):self.status.set('Terminal input queue is busy.');return False
        except ValueError as error:self.status.set(str(error));return False
        return True

    def key(self,event):
        key=event.keysym;control=bool(event.state&4);shift=bool(event.state&1)
        if control and shift and key.lower()=='c':self.copy();return 'break'
        if control and key.lower()=='v' or shift and key=='Insert':self.paste();return 'break'
        if shift and key in ('Prior','Next'):
            self.scroll_view('scroll',-1 if key=='Prior' else 1,'pages');return 'break'
        mapping={'Return':'\r','KP_Enter':'\r','BackSpace':'\x7f','Tab':'\t','ISO_Left_Tab':'\x1b[Z','Escape':'\x1b',
                 'Up':'\x1b[A','Down':'\x1b[B','Right':'\x1b[C','Left':'\x1b[D','Home':'\x1b[H','End':'\x1b[F',
                 'Delete':'\x1b[3~','Insert':'\x1b[2~','Prior':'\x1b[5~','Next':'\x1b[6~',
                 'F1':'\x1bOP','F2':'\x1bOQ','F3':'\x1bOR','F4':'\x1bOS','F5':'\x1b[15~',
                 'F6':'\x1b[17~','F7':'\x1b[18~','F8':'\x1b[19~','F9':'\x1b[20~','F10':'\x1b[21~','F11':'\x1b[23~','F12':'\x1b[24~'}
        value=mapping.get(key,event.char)
        if control and len(key)==1 and '@'<=key.upper()<='_':value=chr(ord(key.upper())&31)
        elif self.screen and (1<<5) in self.screen.mode and key in ('Up','Down','Right','Left','Home','End'):
            value=value.replace('\x1b[','\x1bO')
        elif event.state&8 and event.char:value='\x1b'+event.char
        if value:
            self.clear_selection();self.send(value)
        return 'break'

    def copy(self):
        selection=self.text.tag_ranges('sel')
        if selection:self.root.clipboard_clear();self.root.clipboard_append(self.text.get(*selection))

    def paste(self):
        try:value=self.root.clipboard_get()
        except tk.TclError:return
        if self.screen and (2004<<5) in self.screen.mode:value='\x1b[200~'+value+'\x1b[201~'
        self.clear_selection();self.send(value);self.text.focus_set()

    def select_all(self):
        self.text.tag_add('sel','1.0','end-1c');self.text.tag_raise('sel')

    def selection_start(self,event=None):self.selecting=True

    def selection_end(self,event=None):
        self.selecting=False
        self.root.after_idle(self.flush_redraw)

    def clear_selection(self):
        self.text.tag_remove('sel','1.0','end');self.flush_redraw()

    def flush_redraw(self):
        if not self.closed and self.redraw_pending:self.draw()

    def clear(self):
        self.selecting=False;self.clear_selection()
        if self.screen is not None:
            self.screen.erase_in_display(2);self.screen.cursor_position();self.screen.history.top.clear();self.screen.history.bottom.clear();self.draw();self.send('\x0c')

    def context(self,event):
        if self.menu_open:return 'break'
        self.text.focus_set()
        if self.popup_menu is None or not self.popup_menu.winfo_exists():
            menu=self.popup_menu=tk.Menu(self.text,tearoff=False,
                background='#19212a',foreground='#d1dae4',activebackground='#285578',activeforeground='white')
            menu.add_command(label='Copy',command=self.copy)
            menu.add_command(label='Paste',command=self.paste)
            menu.add_command(label='Select All',command=self.select_all)
            menu.bind('<Unmap>',self.menu_closed)
        menu=self.popup_menu
        menu.entryconfigure('Copy',state='normal' if self.text.tag_ranges('sel') else 'disabled')
        self.menu_open=True
        try:menu.tk_popup(event.x_root+2,event.y_root+2)
        except tk.TclError:
            self.menu_closed()
            raise
        return 'break'

    def menu_closed(self,event=None):
        self.menu_open=False
        if self.popup_menu is not None:
            try:self.popup_menu.grab_release()
            except tk.TclError:pass
        if not self.closed:self.root.after_idle(self.flush_redraw)

    def apply_appearance(self,appearance):
        self.appearance=appearance;family=appearance['font_family']
        if not family or family not in font.families(self.root):family=font.nametofont('TkFixedFont',root=self.root).cget('family')
        self.fixed.configure(family=family,size=appearance['font_size']);self.bold.configure(family=family,size=appearance['font_size'],weight='bold')
        self.text.configure(font=self.fixed,background=appearance['background'],foreground=appearance['foreground'],
                            insertbackground=appearance['foreground'],selectbackground='#285578',
                            selectforeground='#ffffff',inactiveselectbackground='#285578')
        self.styles={};self.resize();self.draw()

    def color(self,value,default):
        if value=='default':return self.appearance[default]
        if value in NAMES:return self.appearance['palette'][NAMES.index(value)]
        if value=='yellow':return self.appearance['palette'][3]
        if len(value)==6 and all(ch in '0123456789abcdefABCDEF' for ch in value):return '#'+value
        return self.appearance[default]

    def draw(self):
        if self.screen is None or self.closed:return
        if self.selecting or self.menu_open or self.text.tag_ranges('sel'):
            self.redraw_pending=True;return
        self.redraw_pending=False
        selection=self.text.tag_ranges('sel');self.text.delete('1.0','end')
        for row in range(self.screen.lines):
            cells=self.screen.buffer[row];run='';last=None
            category=classify_line(''.join(cells[col].data for col in range(self.screen.columns))) if self.appearance['categorize'] else None
            for col in range(self.screen.columns):
                cell=cells[col];style=(cell.fg,cell.bg,cell.bold,cell.underscore,cell.reverse)
                if not self.appearance['ansi']:style=('default','default',False,False,False)
                if category and (cell.fg=='default' or not self.appearance['ansi']):style=(self.appearance[category].lstrip('#'),*style[1:])
                if last is not None and style!=last:self.insert(run,last);run=''
                run+=cell.data;last=style
            if run:self.insert(run,last)
            if row<self.screen.lines-1:self.text.insert('end','\n')
        cursor=self.screen.cursor;prefix=''.join(self.screen.buffer[cursor.y][col].data for col in range(cursor.x))
        self.text.mark_set('insert',f'{cursor.y+1}.{len(prefix.encode("utf-16-le"))//2}')
        self.text.configure(insertontime=0 if cursor.hidden else 600,insertofftime=300)
        if selection:
            try:self.text.tag_add('sel',*selection)
            except tk.TclError:pass
        history=self.screen.history;total=max(1,len(history.top)+self.screen.lines+len(history.bottom))
        self.scroll.set(len(history.top)/total,(len(history.top)+self.screen.lines)/total)
        self.text.tag_raise('sel');self.screen.dirty.clear()

    def insert(self,value,style):
        if style not in self.styles:
            if len(self.styles)>=1024:self.text.insert('end',value);return
            tag='pty_color_'+str(len(self.styles));self.styles[style]=tag
            fg,bg,bold,underline,reverse=style;fg=self.color(fg,'foreground');bg=self.color(bg,'background')
            if reverse:fg,bg=bg,fg
            self.text.tag_configure(tag,foreground=fg,background=bg,font=self.bold if bold else self.fixed,underline=underline)
            self.text.tag_raise('sel')
        self.text.insert('end',value,self.styles[style])

    def resize(self,event=None):
        if self.screen is None:return
        rows=max(2,(self.text.winfo_height()-12)//max(1,self.fixed.metrics('linespace')))
        cols=max(10,(self.text.winfo_width()-12)//max(1,self.fixed.measure('M')))
        rows=min(150,rows);cols=min(300,cols)
        if (rows,cols)!=(self.screen.lines,self.screen.columns):
            self.screen.resize(lines=rows,columns=cols)
            if self.session:self.session.resize(rows,cols)
            self.draw()

    def wheel(self,event):
        self.scroll_view('scroll',-1 if getattr(event,'num',0)==4 or getattr(event,'delta',0)>0 else 1,'pages');return 'break'

    def scroll_view(self,*args):
        if self.screen is None:return
        if args[0]=='moveto':up=float(args[1])<.99
        else:up=int(args[1])<0
        (self.screen.prev_page if up else self.screen.next_page)();self.draw()

    def install(self):
        if self.setup_busy:return
        if self.running:self.status.set('Stop the shell before repairing the engine.');return
        self.setup_busy=True;self.status.set('Installing terminal engine…')
        def setup():
            try:
                commands=[[sys.executable,'-m','venv',str(TOOLS/'.venv')]]
                python=TOOLS/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
                commands.append([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(TOOLS/'requirements.txt')])
                for command in commands:
                    if self.closed:return
                    self.setup_process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
                    try:output,_=self.setup_process.communicate(timeout=180)
                    except subprocess.TimeoutExpired:self.setup_process.kill();self.setup_process.communicate();raise RuntimeError('Terminal setup timed out.')
                    if self.setup_process.returncode:raise RuntimeError(output.decode('utf-8','replace')[-1500:])
                self.setup_messages.put('Terminal engine installed. Terminal → New / Restart Shell is ready.')
            except Exception as error:self.setup_messages.put(str(error))
        threading.Thread(target=setup,daemon=True,name='terminal-setup').start()

    def poll(self):
        if self.closed:return
        try:self.status.set(self.setup_messages.get_nowait());self.setup_busy=False
        except queue.Empty:pass
        dirty=False
        if self.session:
            for _ in range(8):
                try:kind,value=self.session.messages.get_nowait()
                except queue.Empty:break
                if kind=='data' and self.stream:self.stream.feed(value);dirty=True
                elif kind=='started':self.running=True;self.status.set('Interactive shell · '+self.directory)
                elif kind=='error':self.status.set(value)
                elif kind=='exit':self.running=False;self.status.set('Shell exited. Terminal → New / Restart Shell to reconnect.')
        if dirty or self.redraw_pending:self.draw()
        self.job=self.root.after(40,self.poll)

    def cleanup(self):
        if self.closed:return
        self.closed=True;self.root.after_cancel(self.job)
        if self.session:self.session.close()
        if self.setup_process is not None and self.setup_process.poll() is None:self.setup_process.kill()
