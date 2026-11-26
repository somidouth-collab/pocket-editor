"""Search plugin docking, shortcut routing, state sharing and window opacity."""
import json
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins import search_replace


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


class SearchPanelTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.api = core.PluginAPI(self.editor, 'search_replace')
        self.editor.plugin_apis['search_replace'] = self.api
        search_replace.register(self.api)
        self.actions = dict(self.api._commands)

    tearDown = fixtures.PaletteTests.tearDown

    def pump(self, seconds=.3):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.root.update();time.sleep(.005)

    def dock(self):
        self.api._services['show']();self.pump()
        return self.editor.plugin_sidebar.hosts['search_replace']._panel_widget

    def window(self):
        self.actions['Search & Replace — Separate Window']();self.pump(.05)
        return next(w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel) and w.title()=='Search and Replace')

    def entry(self, parent, number, value):
        entry=[w for w in widgets(parent) if isinstance(w,ttk.Entry)][number]
        entry.delete(0,'end');entry.insert(0,value)
        return entry

    def button(self,parent,label):
        next(w for w in widgets(parent) if isinstance(w,ttk.Button) and w.cget('text')==label).invoke()
        self.pump(.05)

    def test_shortcuts_open_full_dock_and_search_using_plugin(self):
        self.editor.set_document(None,'one two one')
        with patch.object(core.simpledialog,'askstring',side_effect=AssertionError('Legacy Find must not open')):
            for key in ('f','h'):
                self.editor.buffer.focus_force();self.root.update()
                self.editor.buffer.event_generate(f'<{self.editor.modifier}-{key}>');self.pump()
                self.assertEqual(self.editor.plugin_sidebar.active,'search_replace')
            panel=self.editor.plugin_sidebar.hosts['search_replace']._panel_widget
            labels={w.cget('text') for w in widgets(panel) if isinstance(w,ttk.Button)}
            self.assertTrue({'Previous','Next','Replace','Replace All','Open separate window'} <= labels)
            self.entry(panel,0,'one');self.editor.buffer.mark_set('insert','1.0')
            self.editor.find_next();self.pump()
            self.assertEqual(self.editor.buffer.get('sel.first','sel.last'),'one')
            self.assertEqual(self.editor.buffer.index('insert'),'1.0')
            self.editor.find_next();self.pump();self.assertEqual(self.editor.buffer.index('insert'),'1.8')

    def test_dock_and_window_share_inputs_and_undoable_replacement(self):
        self.editor.set_document(None,'blue blue');panel=self.dock()
        self.entry(panel,0,'blue');self.entry(panel,1,'green')
        window=self.window()
        entries=[w for w in widgets(window) if isinstance(w,ttk.Entry)]
        self.assertEqual([w.get() for w in entries],['blue','green'])
        self.button(window,'Replace All');self.assertEqual(self.api.get_text(),'green green')
        self.editor.buffer.edit_undo();self.assertEqual(self.api.get_text(),'blue blue')
        self.entry(window,1,'red')
        self.assertEqual([w for w in widgets(panel) if isinstance(w,ttk.Entry)][1].get(),'red')
        self.button(window,'Use vertical tab');self.assertFalse(window.winfo_exists())
        self.assertTrue(panel.winfo_ismapped())

    def test_opacity_is_persisted_and_never_applied_to_editor(self):
        window=self.window();scale=next(w for w in widgets(window) if isinstance(w,ttk.Scale))
        with patch.object(window,'attributes', wraps=window.attributes) as attributes,patch.object(self.root,'attributes', wraps=self.root.attributes) as root_attributes:
            scale.set(65);self.pump(.05);scale.event_generate('<ButtonRelease-1>');self.pump(.05)
            self.assertTrue(any(call.args==('-alpha',.65) for call in attributes.call_args_list))
            self.assertFalse(any(call.args and call.args[0]=='-alpha' for call in root_attributes.call_args_list))
        settings=self.editor.settings_path.with_name('search_replace_settings.json')
        self.assertEqual(json.loads(settings.read_text())['opacity'],65)
        panel=self.dock();self.assertAlmostEqual(next(w for w in widgets(panel) if isinstance(w,ttk.Scale)).get(),65)

    def test_narrow_dock_scrolls_to_bottom_controls(self):
        panel=self.dock();self.root.geometry('700x400');self.pump()
        sidebar=self.editor.plugin_sidebar;sidebar.panes.sashpos(0,240);self.pump()
        canvas=next(w for w in widgets(panel) if isinstance(w,tk.Canvas))
        canvas.yview_moveto(1);self.pump()
        scale=next(w for w in widgets(panel) if isinstance(w,ttk.Scale))
        self.assertGreater(canvas.yview()[0],0)
        self.assertGreaterEqual(scale.winfo_rooty(),canvas.winfo_rooty())
        self.assertLessEqual(scale.winfo_rooty()+scale.winfo_height(),canvas.winfo_rooty()+canvas.winfo_height())
        self.assertFalse(any(isinstance(w,tk.Toplevel) for w in self.root.winfo_children()))
