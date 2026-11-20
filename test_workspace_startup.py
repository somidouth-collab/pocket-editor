"""Local full-window startup, close/reopen, and stable menu checks."""
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
import pocket_editor as core
from editor_session import EditorSession
import test_editor as fixtures


class WorkspaceTests(unittest.TestCase):
    setUp=fixtures.EditorTests.setUp
    tearDown=fixtures.EditorTests.tearDown
    def pump(self, seconds=.2):
        fixtures.EditorTests.pump(self, seconds)

    def tree(self):return next(w for w in fixtures.widgets(self.root) if isinstance(w,ttk.Treeview))

    def test_last_project_expansion_drafts_and_split_survive_clean_restart(self):
        project=self.folder/'work';nested=project/'nested';nested.mkdir(parents=True)
        file=nested/'draft.py';file.write_text('original')
        session=EditorSession(self.editor,self.folder/'session.json');self.assertTrue(session.start())
        api=self.editor.plugin_apis['project_explorer'];api.call_service('project_explorer.open_project',project)
        tree=self.tree();root_item=tree.get_children('')[0];child=tree.get_children(root_item)[0]
        tree.focus(child);tree.selection_set(child);tree.item(child,open=True);tree.event_generate('<<TreeviewOpen>>');self.pump()
        file_item=tree.get_children(child)[0];tree.selection_set(file_item);tree.focus(file_item)
        self.editor.open_path(file);self.editor.buffer.insert('end-1c',' edited')
        self.editor.new();self.editor.buffer.insert('1.0','untitled draft')
        self.editor.split_editor.enable();self.pump()
        with patch.object(core.messagebox,'askyesnocancel') as prompt:self.editor.quit();prompt.assert_not_called()
        self.assertEqual(file.read_text(),'original')
        data=json.loads(session.path.read_text());self.assertTrue(data['clean'])
        self.root=tk.Tk();self.root.report_callback_exception=lambda *args:self.errors.append(str(args[1]))
        self.editor=core.Editor(self.root,False)
        deadline=time.monotonic()+10
        while not self.editor.plugins_ready and time.monotonic()<deadline:self.pump(.01)
        self.assertEqual(self.editor.plugin_apis['project_explorer'].call_service('project_explorer.get_project'),project)
        restored=EditorSession(self.editor,self.folder/'session.json')
        with patch.object(core.messagebox,'askyesno') as prompt:self.assertTrue(restored.start());prompt.assert_not_called()
        self.pump()
        texts={doc['widget'].get('1.0','end-1c') for doc in self.editor.documents}
        self.assertEqual(texts,{'original edited','untitled draft'})
        self.assertTrue(self.editor.split_editor.enabled)
        tree=self.tree();root_item=tree.get_children('')[0];child=tree.get_children(root_item)[0]
        self.assertTrue(tree.item(child,'open'))
        self.assertEqual(tree.item(tree.selection()[0],'text'),'draft.py')
        self.assertEqual(file.read_text(),'original')

    def test_right_button_release_posts_menu_without_opening_a_file(self):
        project=self.folder/'menu-project';project.mkdir();(project/'one.txt').write_text('one')
        self.editor.plugin_apis['project_explorer'].call_service('project_explorer.open_project',project);self.pump()
        tree=self.tree();row=tree.get_children(tree.get_children('')[0])[0];y=tree.bbox(row)[1]+3
        menu=next(w for w in fixtures.widgets(tree) if isinstance(w,tk.Menu))
        tree.event_generate('<Button-3>',x=15,y=y);self.pump()
        self.assertFalse(menu.winfo_ismapped());self.assertIsNone(self.editor.path)
        tree.event_generate('<ButtonRelease-3>',x=15,y=y);self.pump(.15)
        self.assertTrue(menu.winfo_ismapped());self.assertIsNone(self.editor.path)
        old_rows=tree.get_children(tree.get_children('')[0])
        (project/'two.txt').write_text('two')
        self.editor.dispatch_plugin_event('project_changed','another_plugin',{})
        self.pump()
        self.assertTrue(menu.winfo_ismapped())
        self.assertEqual(tree.get_children(tree.get_children('')[0]),old_rows)
        menu.invoke('Open');self.assertEqual(self.editor.path,project/'one.txt')
        menu.unpost();self.pump();self.assertIsNone(menu.grab_current())
        self.assertIn('two.txt',[tree.item(item,'text') for item in tree.get_children(tree.get_children('')[0])])


class FirstPaintTests(unittest.TestCase):
    def test_editor_paints_before_incremental_plugin_registration(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'SETTINGS_PATH',Path(folder)/'settings.json'):
            root=tk.Tk();errors=[];root.report_callback_exception=lambda *args:errors.append(str(args[1]))
            editor=core.Editor(root,False)
            root.update_idletasks()
            self.assertTrue(root.winfo_ismapped());self.assertFalse(editor.plugins_ready)
            self.assertEqual(editor.loaded_plugins,set())
            observed=[]
            def observe():
                api=editor.plugin_apis.get('loading_indicator')
                if api and api.call_service('loading_indicator.status'):observed.append(True)
                if not editor.plugins_ready:root.after(1,observe)
            root.after(1,observe)
            deadline=time.monotonic()+10
            while not editor.plugins_ready and time.monotonic()<deadline:
                root.update()
                time.sleep(.002)
            self.assertTrue(editor.plugins_ready);self.assertTrue(observed)
            self.assertTrue(editor.plugin_load_times);self.assertFalse(errors)
            editor.quit()
