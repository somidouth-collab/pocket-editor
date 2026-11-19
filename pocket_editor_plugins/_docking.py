"""A dockable plugin surface; dialogs may still use normal Toplevel windows."""
import tkinter as tk


class DockedPanel(tk.Frame):
    _docked_panel=True

    def __init__(self,parent):
        super().__init__(parent,background='#11161c')
        self.pack(fill='both',expand=True)

    def title(self,value=None):return value or ''
    def geometry(self,value=None):return ''
    def minsize(self,*args):pass
    def resizable(self,*args):pass
    def transient(self,*args):pass
    def protocol(self,*args):pass
    def withdraw(self):
        callback=getattr(self.master,'_on_hide',None)
        if callback:callback()
        else:self.pack_forget()
    def deiconify(self):
        self.pack(fill='both',expand=True)
        callback=getattr(self.master,'_on_reveal',None)
        if callback:callback()


def create_window(root,parent=None):
    return DockedPanel(parent) if parent is not None else tk.Toplevel(root)
