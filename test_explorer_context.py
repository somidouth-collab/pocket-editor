"""Explorer icon layout and exact-target context actions."""
from pathlib import Path
from tkinter import ttk
import unittest
from unittest.mock import patch
import test_editor as fixtures
from pocket_editor_plugins._explorer_icons import IconButton


class ExplorerContextTests(unittest.TestCase):
    setUp=fixtures.EditorTests.setUp
    tearDown=fixtures.EditorTests.tearDown
    pump=fixtures.EditorTests.pump

    def test_icons_one_row_and_context_states_follow_the_clicked_item(self):
        folder=self.folder/'project';folder.mkdir()
        (folder/'first.txt').write_text('first');(folder/'second.txt').write_text('second')
        (folder/'subfolder').mkdir()
        self.editor.plugin_apis['project_explorer'].call_service('project_explorer.open_project',folder)
        self.pump()
        tree=next(w for w in fixtures.widgets(self.root) if isinstance(w,ttk.Treeview))
        icons=[w for w in fixtures.widgets(self.root) if isinstance(w,IconButton)]
        self.assertEqual(len(icons),7)
        self.assertEqual(len({w.winfo_rooty() for w in icons}),1)
        self.assertTrue(all(w.winfo_width()<=30 for w in icons))
        root_item=tree.get_children('')[0]
        rows={tree.item(row,'text').rstrip('/'):row for row in tree.get_children(root_item)}
        tree.selection_set(rows['first.txt'])
        # Patch the class method with autospec so the menu itself is captured.
        with patch('tkinter.Menu.tk_popup',autospec=True) as popup:
            y=tree.bbox(rows['second.txt'])[1]+3
            tree.event_generate('<ButtonRelease-3>',x=10,y=y);self.pump()
            menu=popup.call_args.args[0]
        self.assertEqual(tree.selection(),(rows['second.txt'],))
        self.assertEqual(menu.entrycget('Delete…','state'),'normal')
        menu.invoke('Copy Path');self.assertEqual(self.root.clipboard_get(),str(folder/'second.txt'))
        with patch('tkinter.Menu.tk_popup',autospec=True):
            y=tree.bbox(root_item)[1]+3;tree.event_generate('<ButtonRelease-3>',x=10,y=y);self.pump()
        self.assertEqual(menu.entrycget('Rename…','state'),'disabled')
        self.assertEqual(menu.entrycget('Delete…','state'),'disabled')
        with patch('tkinter.Menu.tk_popup',autospec=True):
            tree.event_generate('<ButtonRelease-3>',x=10,y=tree.winfo_height()-4);self.pump()
        self.assertFalse(tree.selection())
        self.assertEqual(menu.entrycget('Open','state'),'disabled')
        self.assertEqual(menu.entrycget('Delete…','state'),'disabled')
        self.assertEqual(menu.entrycget('New File…','state'),'normal')
        tree.focus(rows['subfolder']);tree.focus_force();self.pump()
        with patch('tkinter.Menu.tk_popup',autospec=True):
            tree.event_generate('<Shift-F10>');self.pump()
        # Keyboard context must target the focused folder too.
        self.assertEqual(tree.selection(),(rows['subfolder'],))

        menu.invoke('Open');self.pump()
        self.assertTrue(tree.item(rows['subfolder'],'open'))
        menu.invoke('Open');self.pump()
        self.assertFalse(tree.item(rows['subfolder'],'open'))
