"""Real GUI checks for selected vertical tabs, docking and persistence."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock
import pocket_editor as core
import test_editor as fixtures


class SidebarTests(unittest.TestCase):
    setUp=fixtures.EditorTests.setUp
    tearDown=fixtures.EditorTests.tearDown
    pump=fixtures.EditorTests.pump

    def test_explorer_layout_and_saved_plugin_choices(self):
        sidebar=self.editor.plugin_sidebar
        self.assertTrue(sidebar.visible)
        self.assertEqual(sidebar.active,'project_explorer')
        self.assertTrue(sidebar.hosts['project_explorer']._panel_widget.winfo_ismapped())
        sidebar.choose();self.pump()
        for owner,value in sidebar.choices.items():value.set(owner in ('codex_assistant','python_tools'))
        next(w for w in fixtures.widgets(sidebar.chooser) if isinstance(w,ttk.Button) and w.cget('text')=='Save tabs').invoke()
        self.pump()
        self.assertEqual(set(sidebar.pins),{'codex_assistant','python_tools'})
        self.assertFalse(sidebar.visible)
        saved=core.read_settings()
        self.assertEqual(saved['sidebar_plugins'],sidebar.pins)
        self.assertIsNone(saved['sidebar_active'])
        self.assertIn('project_explorer',self.editor.plugin_apis)

    def test_docked_panels_retain_state_and_collapse(self):
        sidebar=self.editor.plugin_sidebar
        sidebar.open('ai_assistant');self.pump()
        app=next(cb.__self__ for label,cb in self.editor.plugin_apis['ai_assistant']._commands if label=='AI Assistant…')
        original=app.window
        self.assertTrue(getattr(original,'_docked_panel',False))
        app.prompt.insert('1.0','Keep this draft')
        sidebar.open('codex_assistant');self.pump()
        self.assertTrue(original.winfo_exists())
        sidebar.open('ai_assistant');self.pump()
        self.assertIs(app.window,original)
        self.assertEqual(app.prompt.get('1.0','end-1c'),'Keep this draft')
        sidebar.toggle('ai_assistant');self.assertFalse(sidebar.visible)
        app.show();self.pump();self.assertTrue(sidebar.visible)
        self.assertEqual(sidebar.active,'ai_assistant')

    def test_selecting_an_action_plugin_does_not_run_commands(self):
        api=core.PluginAPI(self.editor,'safe_actions');callback=Mock()
        api.add_command('Explicit action',callback)
        self.editor.plugin_apis['safe_actions']=api
        self.editor.plugin_sidebar.open('safe_actions');self.pump()
        callback.assert_not_called()
        host=self.editor.plugin_sidebar.hosts['safe_actions']
        next(w for w in fixtures.widgets(host) if isinstance(w,ttk.Button)).invoke()
        callback.assert_called_once()
