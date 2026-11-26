"""Real Tk document context-menu targeting, clipboard, selection and history."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import tkinter as tk
import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins import editor_commands, search_replace


class DocumentContextTests(unittest.TestCase):
    setUp=fixtures.PaletteTests.setUp
    tearDown=fixtures.PaletteTests.tearDown

    def popup(self,index='1.0',keyboard=False,widget=None):
        text=widget or self.editor.buffer.widget
        self.root.update()
        box=text.bbox(index)
        x,y=(box[0]+2,box[1]+2) if box else (5,5)
        event=SimpleNamespace(widget=text,x=x,y=y,x_root=text.winfo_rootx()+x,y_root=text.winfo_rooty()+y)
        with patch.object(tk.Menu,'tk_popup') as posted:
            self.assertEqual(self.editor.document_context(event,keyboard),'break')
            posted.assert_called_once()
        return self.editor.document_context_menu

    def test_selection_clipboard_delete_and_undo(self):
        e=self.editor;e.set_document(None,'hello world')
        e.buffer.tag_add('sel','1.0','1.5')
        menu=self.popup('1.2')
        self.assertEqual(e.buffer.get('sel.first','sel.last'),'hello')
        menu.invoke('Copy');self.assertEqual(self.root.clipboard_get(),'hello')
        menu.invoke('Delete Selection');self.assertEqual(e.buffer.get('1.0','end-1c'),' world')
        self.popup(keyboard=True).invoke('Undo');self.assertEqual(e.buffer.get('1.0','end-1c'),'hello world')
        self.popup(keyboard=True).invoke('Select All')
        self.root.clipboard_clear();self.root.clipboard_append('replacement')
        self.popup(keyboard=True).invoke('Paste');self.assertEqual(e.buffer.get('1.0','end-1c'),'replacement')
        self.popup(keyboard=True).invoke('Undo');self.assertEqual(e.buffer.get('1.0','end-1c'),'hello world')

    def test_disabled_items_and_keyboard_menu_preserve_caret(self):
        e=self.editor;e.set_document(None,'one two');e.buffer.mark_set('insert','1.4')
        menu=self.popup(keyboard=True)
        self.assertEqual(e.buffer.index('insert'),'1.4')
        for label in ('Undo','Redo','Cut','Copy','Delete Selection','Copy File Path','Find…','Replace…'):
            self.assertEqual(menu.entrycget(label,'state'),'disabled',label)
        e.buffer.configure(state='disabled');menu=self.popup(keyboard=True)
        self.assertEqual(menu.entrycget('Paste','state'),'disabled')
        e.buffer.configure(state='normal')

    def test_click_outside_selection_moves_caret_without_editing(self):
        e=self.editor;e.set_document(None,'hello world')
        e.buffer.tag_add('sel','1.0','1.5')
        self.popup('1.8')
        self.assertFalse(e.buffer.tag_ranges('sel'))
        self.assertEqual(e.buffer.index('insert'),'1.8')
        self.assertEqual(e.buffer.get('1.0','end-1c'),'hello world')
        self.assertFalse(e.buffer.edit_modified())

    def test_split_pane_menu_targets_clicked_document(self):
        e=self.editor;e.set_document(None,'left');left=e.active_document
        e.new();e.set_document(None,'right');right=e.active_document
        e.split_editor.enable();self.root.update()
        self.assertIs(e.active_document,right)
        menu=self.popup(widget=left['widget'])
        self.assertIs(e.active_document,left)
        menu.invoke('Select All');menu=self.popup(keyboard=True,widget=left['widget']);menu.invoke('Cut')
        self.assertEqual(left['widget'].get('1.0','end-1c'),'')
        self.assertEqual(right['widget'].get('1.0','end-1c'),'right')
        menu=self.popup(keyboard=True,widget=left['widget']);menu.invoke('Undo')
        self.assertEqual(left['widget'].get('1.0','end-1c'),'left')

    def test_plugin_commands_and_file_path(self):
        e=self.editor
        for name,module in (('editor_commands',editor_commands),('search_replace',search_replace)):
            api=core.PluginAPI(e,name);e.plugin_apis[name]=api;module.register(api)
        path=core.SETTINGS_PATH.parent/'example.py';e.set_document(path,'line')
        menu=self.popup()
        menu.invoke('Copy File Path');self.assertEqual(self.root.clipboard_get(),str(path))
        menu.invoke('Duplicate Line / Selection');self.assertEqual(e.buffer.get('1.0','end-1c'),'line\nline')
        callback=e.plugin_apis['search_replace']._services['show']
        with patch.object(e,'run_plugin') as run:
            menu.invoke('Find…');run.assert_called_once_with('search_replace',callback)
        for sequence in ('<Button-3>','<Shift-F10>','<KeyPress-Menu>'):
            self.assertTrue(e.buffer.widget.bind(sequence))

    def test_dismiss_releases_grab_and_reopening_destroys_old_menu(self):
        text=self.editor.buffer.widget
        self.root.update()
        event=SimpleNamespace(widget=text,x=5,y=5,x_root=text.winfo_rootx()+5,y_root=text.winfo_rooty()+5)
        if self.root.tk.call('tk', 'windowingsystem') == 'win32':
            # TrackPopupMenu blocks until human input on Windows. Exercise the
            # same registered dismissal handler with a controlled native grab.
            old=self.popup()
            old.grab_set()
            old.event_generate('<Unmap>');self.root.update()
        else:
            self.editor.document_context(event);self.root.update()
            old=self.editor.document_context_menu;old.unpost();self.root.update()
        self.assertIsNone(self.root.grab_current())
        self.popup();self.assertFalse(old.winfo_exists())
