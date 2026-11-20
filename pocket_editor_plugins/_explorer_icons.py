"""Small keyboard-accessible vector actions for Project Explorer."""
import tkinter as tk


class IconButton(tk.Canvas):
    def __init__(self,parent,action,label,command):
        super().__init__(parent,width=26,height=26,background='#11161c',highlightthickness=1,
                         highlightbackground='#11161c',highlightcolor='#1597f5',takefocus=True)
        self.action,self.tooltip_text,self.command=action,label,command
        self.tip=None
        color='#b8c6d4'
        def line(*coords):self.create_line(*coords,fill=color,width=1.5)
        if action in ('open','new_folder'):
            line(5,8,5,20,22,20,22,10,13,10,11,7,5,7)
        elif action=='save':
            self.create_rectangle(6,5,21,21,outline=color,width=1.5)
            self.create_rectangle(9,5,18,11,outline=color);self.create_rectangle(9,15,18,21,outline=color)
        elif action in ('load','new_file'):
            line(7,5,17,5,21,9,21,22,7,22,7,5);line(16,5,16,10,21,10)
        elif action=='refresh':
            self.create_arc(5,5,22,22,start=30,extent=290,outline=color,width=1.5,style='arc');line(23,4,22,10,17,8)
        else:
            for x in (7,13,19):self.create_oval(x-1,12,x+1,14,fill=color,outline='')
        if action in ('new_file','new_folder'):line(18,15,24,15);line(21,12,21,18)
        if action=='load':line(11,14,17,14);line(14,11,14,18);line(11,15,14,18,17,15)
        self.bind('<ButtonRelease-1>',lambda event:self.invoke())
        self.bind('<Return>',lambda event:self.invoke())
        self.bind('<space>',lambda event:self.invoke())
        self.bind('<Enter>',self.enter);self.bind('<Leave>',self.leave)
        self.bind('<FocusIn>',self.enter);self.bind('<FocusOut>',self.leave)
        self.bind('<Destroy>',lambda event:self.hide_tip())

    def invoke(self):
        self.hide_tip();self.command();return 'break'

    def enter(self,event=None):
        self.configure(background='#19212a');self.hide_tip()
        self.tip=tk.Toplevel(self);self.tip.overrideredirect(True)
        tk.Label(self.tip,text=self.tooltip_text,background='#253342',foreground='#f0f6fc',padx=8,pady=5).pack()
        self.tip.geometry('+%d+%d'%(self.winfo_rootx(),self.winfo_rooty()+30))

    def hide_tip(self):
        if self.tip is not None:
            try:self.tip.destroy()
            except tk.TclError:pass
            self.tip=None

    def leave(self,event=None):
        self.hide_tip();self.configure(background='#11161c')
