"""Cross-platform Tk regressions for Windows text/clipboard binding failures."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock

import pocket_editor as core


class TextBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root=core.create_root();self.changed=Mock();self.errors=[]
        self.root.report_callback_exception=lambda *args:self.errors.append(str(args[1]))
        self.text=core.DocumentText(self.root,self.changed,undo=True)
        self.text.pack();self.text.focus_force();self.root.update()
    def tearDown(self):
        self.root.destroy();self.assertEqual(self.errors,[])
    def tcl_event(self,script):
        self.root.tk.call('after',0,script)
        self.root.after(60,self.root.quit)
        self.root.mainloop()
    def native_windows_paste(self):
        # Execute Tk's actual Windows branch even on the Linux development host.
        # No global Tk platform commands or bindings are monkey-patched.
        body=self.root.tk.call('info','body','tk_textPaste')
        body=body.replace('[tk windowingsystem]','"win32"')
        self.root.tk.call('proc','::pocket_test_windows_paste','w',body)
        self.tcl_event('::pocket_test_windows_paste '+str(self.text))

    def test_windows_paste_without_selection_does_not_escape_mainloop(self):
        self.root.clipboard_clear();self.root.clipboard_append('hello café 😀\nnext line')
        self.native_windows_paste()
        self.assertEqual(self.text.get('1.0','end-1c'),'hello café 😀\nnext line')
        self.text.edit_undo();self.assertEqual(self.text.get('1.0','end-1c'),'')
        self.text.edit_redo();self.assertEqual(self.text.get('1.0','end-1c'),'hello café 😀\nnext line')

    def test_windows_paste_replaces_selection_in_one_undo(self):
        self.text.insert('1.0','before old after');self.text.edit_reset();self.changed.reset_mock()
        self.text.tag_add('sel','1.7','1.10');self.text.mark_set('insert','1.10')
        self.root.clipboard_clear();self.root.clipboard_append('new')
        self.native_windows_paste()
        self.assertEqual(self.text.get('1.0','end-1c'),'before new after')
        self.text.edit_undo();self.assertEqual(self.text.get('1.0','end-1c'),'before old after')
        self.text.edit_redo();self.assertEqual(self.text.get('1.0','end-1c'),'before new after')

    def test_copy_cut_clear_and_selection_queries_without_selection_are_safe(self):
        self.text.insert('1.0','keep');self.text.edit_reset();self.changed.reset_mock()
        self.root.clipboard_clear();self.root.clipboard_append('clipboard unchanged')
        name=str(self.text)
        script='; '.join(['tk_textCopy '+name,'tk_textCut '+name,
                         'catch {'+name+' delete sel.first sel.last}',
                         'catch {'+name+' get sel.first sel.last}',
                         'catch {'+name+' index sel.first}',
                         'catch {'+name+' edit undo}','catch {'+name+' edit redo}'])
        self.tcl_event(script)
        self.assertEqual(self.text.get('1.0','end-1c'),'keep')
        self.assertEqual(self.root.clipboard_get(),'clipboard unchanged')
        self.changed.assert_not_called()

    def test_native_errors_are_catchable_in_tcl_and_still_raise_for_python_callers(self):
        name=str(self.text)
        self.tcl_event('set ::native_error [catch {'+name+' index sel.first} ::native_message]')
        self.assertEqual(int(self.root.tk.getvar('::native_error')),1)
        self.assertTrue(self.root.tk.getvar('::native_message'))
        with self.assertRaises(tk.TclError):self.text.index('sel.first')
        with self.assertRaises(tk.TclError):self.text.tk.call(name,'edit','undo','extra-argument')
        self.changed.assert_not_called()

    def test_unavailable_clipboard_and_readonly_paste_preserve_document(self):
        self.text.insert('1.0','keep');self.text.edit_reset()
        self.root.clipboard_clear()
        self.native_windows_paste();self.assertEqual(self.text.get('1.0','end-1c'),'keep')
        self.root.clipboard_append('replacement');self.text.configure(state='disabled')
        self.native_windows_paste();self.assertEqual(self.text.get('1.0','end-1c'),'keep')

    def test_destroy_cleans_alias_original_and_observer_commands(self):
        name=str(self.text);original=self.text._original;observer=self.text._observer
        self.text.destroy()
        for command in (name,original,observer):self.assertFalse(self.root.tk.call('info','commands',command))
        other=core.DocumentText(self.root,self.changed,undo=True)
        other.insert('1.0','still works');self.assertEqual(other.get('1.0','end-1c'),'still works')

    def test_widget_names_and_text_are_never_tcl_script_interpolated(self):
        unusual=core.DocumentText(self.root,self.changed,undo=True,name='text[brackets]$dollar')
        unusual.insert('1.0','[error injected] $value {literal}')
        self.assertEqual(unusual.get('1.0','end-1c'),'[error injected] $value {literal}')
        name=str(unusual);unusual.destroy()
        self.assertNotIn(name,self.root.tk.splitlist(self.root.tk.call('info','commands')))

    def test_real_ctrl_v_ctrl_z_shortcuts(self):
        self.root.clipboard_clear();self.root.clipboard_append('keyboard paste')
        self.text.event_generate('<Control-KeyPress-v>');self.root.update()
        self.assertEqual(self.text.get('1.0','end-1c'),'keyboard paste')
        self.text.event_generate('<Control-KeyPress-z>');self.root.update()
        self.assertEqual(self.text.get('1.0','end-1c'),'')
        for _ in range(5):self.text.event_generate('<Control-KeyPress-z>');self.root.update()
        self.assertEqual(self.text.get('1.0','end-1c'),'')


class EditMenuFocusTests(unittest.TestCase):
    def setUp(self):
        import test_command_palette as fixtures
        fixtures.PaletteTests.setUp(self)
    def tearDown(self):
        import test_command_palette as fixtures
        self.editor.buffer.edit_modified(False);self.editor.rich_dirty=False
        fixtures.PaletteTests.tearDown(self)

    def test_menu_paste_targets_focused_entry_instead_of_modifying_document(self):
        self.editor.buffer.insert('1.0','document unchanged')
        entry=ttk.Entry(self.root);entry.pack();entry.focus_force();self.root.update()
        self.root.clipboard_clear();self.root.clipboard_append('input value')
        menu=next(self.root.nametowidget(self.editor.menu_bar.entrycget(index,'menu'))
                  for index in range(self.editor.menu_bar.index('end')+1)
                  if self.editor.menu_bar.type(index)=='cascade' and self.editor.menu_bar.entrycget(index,'label')=='Edit')
        menu.invoke('Paste');self.root.update()
        self.assertEqual(entry.get(),'input value')
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'document unchanged')
        entry.selection_range(0,'end');menu.invoke('Copy');self.root.update()
        self.assertEqual(self.root.clipboard_get(),'input value')

    def test_unwrapped_native_menu_focus_falls_back_to_document(self):
        from unittest.mock import patch
        self.root.clipboard_clear();self.root.clipboard_append('fallback paste')
        with patch.object(self.root,'focus_get',side_effect=KeyError('native menu clone')):
            self.editor.edit_event('Paste')
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'fallback paste')

    def test_menu_undo_and_paste_follow_focused_split_document(self):
        editor=self.editor
        editor.buffer.insert('1.0','left');left=editor.active_document;editor.buffer.edit_reset()
        editor.new();editor.buffer.insert('1.0','right');right=editor.active_document;editor.buffer.edit_reset()
        editor.split_editor.enable();self.root.update()
        left['widget'].focus_force();self.root.update()
        self.assertIs(editor.active_document,left)
        left['widget'].mark_set('insert','end-1c')
        self.root.clipboard_clear();self.root.clipboard_append(' changed')
        editor.edit_event('Paste');self.root.update()
        self.assertEqual(left['widget'].get('1.0','end-1c'),'left changed')
        self.assertEqual(right['widget'].get('1.0','end-1c'),'right')
        editor.edit_event('Undo');self.root.update()
        self.assertEqual(left['widget'].get('1.0','end-1c'),'left')
        for _ in range(3):editor.edit_event('Undo');self.root.update()
        self.assertEqual(right['widget'].get('1.0','end-1c'),'right')


class WindowsFormatterProcessTests(unittest.TestCase):
    def test_black_runs_without_a_console_window_on_windows(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from pocket_editor_plugins import _python_formatter as formatter
        with patch.object(formatter.sys,'platform','win32'),patch.object(formatter.subprocess,'CREATE_NO_WINDOW',0x08000000,create=True),patch.object(formatter.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='x = 1\n',stderr='')) as run:
            result=formatter.format_source('x=1','example.py',88)
        self.assertEqual(result,'x = 1\n')
        self.assertEqual(run.call_args.kwargs['creationflags'],0x08000000)
