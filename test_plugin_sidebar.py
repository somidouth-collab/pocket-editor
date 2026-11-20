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
    until=fixtures.EditorTests.until
    def pump(self, seconds=.2):
        fixtures.EditorTests.pump(self, seconds)

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
        self.until(lambda:app.window is not None)
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

    def test_loading_is_visible_before_first_factory_and_cancels_hidden_build(self):
        sidebar=self.editor.plugin_sidebar
        api=core.PluginAPI(self.editor,'test_panel')
        seen=[]
        def factory(parent):
            seen.append((parent.winfo_ismapped(),bool(self.editor.plugin_apis['loading_indicator'].call_service('loading_indicator.status'))))
            ttk.Label(parent,text='Ready').pack()
        api.add_panel(factory);self.editor.plugin_apis['test_panel']=api
        sidebar.open('test_panel');self.assertFalse(seen)
        self.root.update_idletasks()
        self.assertIn('test_panel',sidebar.pending)
        self.pump(.2)
        self.assertEqual(seen,[(True,True)])
        self.assertNotIn('test_panel',sidebar.pending)
        self.assertFalse(self.editor.plugin_apis['loading_indicator'].call_service('loading_indicator.status'))
        other=core.PluginAPI(self.editor,'cancel_panel');callback=Mock();other.add_panel(callback)
        self.editor.plugin_apis['cancel_panel']=other
        sidebar.open('cancel_panel');sidebar.hide();self.pump(.06)
        callback.assert_not_called();self.assertNotIn('cancel_panel',sidebar.pending)
        self.assertFalse(self.editor.plugin_apis['loading_indicator'].call_service('loading_indicator.status'))

    def test_panel_loader_waits_for_asynchronous_readiness_and_geometry(self):
        sidebar=self.editor.plugin_sidebar;api=core.PluginAPI(self.editor,'async_panel')
        created=[]
        def factory(parent):
            panel=ttk.Frame(parent);panel._loading_ready=False;panel.pack(fill='both',expand=True)
            created.append(panel)
            self.root.after(300,lambda:setattr(panel,'_loading_ready',True))
            return panel
        api.add_panel(factory);self.editor.plugin_apis['async_panel']=api
        sidebar.open('async_panel');self.until(lambda:bool(created))
        self.pump(.08)
        self.assertIn('async_panel',sidebar.pending)
        self.assertTrue(self.editor.plugin_apis['loading_indicator'].call_service('loading_indicator.status'))
        sidebar.hide();sidebar.open('async_panel');self.pump(.02)
        self.assertEqual(len(created),1);self.assertIn('async_panel',sidebar.pending)
        self.assertTrue(self.editor.plugin_apis['loading_indicator'].call_service('loading_indicator.status'))
        self.until(lambda:'async_panel' not in sidebar.pending)
        self.assertTrue(created[0].winfo_ismapped())
        self.assertFalse(self.editor.plugin_apis['loading_indicator'].call_service('loading_indicator.status'))

    def test_loading_finish_keeps_other_pending_operations_and_cancels_on_cleanup(self):
        api=self.editor.plugin_apis['loading_indicator'];ready=[False]
        first=api.call_service('loading_indicator.start',message='Waiting for data')
        second=api.call_service('loading_indicator.start',message='Ready to draw')
        api.call_service('loading_indicator.finish',first,ready=lambda:ready[0])
        api.call_service('loading_indicator.finish',second)
        self.assertIn(second,api.call_service('loading_indicator.status'))
        self.until(lambda:second not in api.call_service('loading_indicator.status'))
        self.assertIn(first,api.call_service('loading_indicator.status'))
        ready[0]=True;self.until(lambda:first not in api.call_service('loading_indicator.status'))
        cancelled=api.call_service('loading_indicator.start',message='Cancelled work')
        api.call_service('loading_indicator.finish',cancelled,ready=lambda:False)
        api.call_service('loading_indicator.stop',cancelled);self.pump(.04)
        self.assertNotIn(cancelled,api.call_service('loading_indicator.status'))

    def test_first_window_command_paints_loading_before_callback(self):
        api=core.PluginAPI(self.editor,'test_window');self.editor.plugin_apis['test_window']=api
        seen=[]
        def show():seen.append(bool(self.editor.plugin_apis['loading_indicator'].call_service('loading_indicator.status')))
        self.editor.run_plugin('test_window.py',show)
        self.assertEqual(seen,[])
        self.root.update_idletasks();self.pump(.06)
        self.assertEqual(seen,[True])
        self.editor.run_plugin('test_window.py',show)
        self.assertEqual(seen,[True,False])

    def test_scrollable_picker_keeps_save_visible_in_small_window(self):
        sidebar=self.editor.plugin_sidebar
        for index in range(80):
            owner='extra_'+str(index)
            self.editor.plugin_apis[owner]=core.PluginAPI(self.editor,owner)
        sidebar.choose();sidebar.chooser.geometry('440x360');self.pump()
        save=next(w for w in fixtures.widgets(sidebar.chooser) if isinstance(w,ttk.Button) and w.cget('text')=='Save tabs')
        self.assertTrue(save.winfo_ismapped())
        self.assertLessEqual(save.winfo_rooty()+save.winfo_height(),sidebar.chooser.winfo_rooty()+sidebar.chooser.winfo_height())
        canvas=sidebar.chooser_canvas
        self.assertEqual(canvas.cget('background'),ttk.Style(self.root).lookup('TFrame','background'))
        self.assertLess(canvas.yview()[1],1)
        canvas.event_generate('<MouseWheel>',delta=-120);self.pump();self.assertGreater(canvas.yview()[0],0)
        canvas.yview_moveto(1);self.pump()
        self.assertTrue(save.winfo_ismapped())
        sidebar.choices['extra_79'].set(True);save.invoke();self.pump()
        self.assertIn('extra_79',core.read_settings()['sidebar_plugins'])
