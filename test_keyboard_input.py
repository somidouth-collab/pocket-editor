"""Undo Tcl-boundary safety and process-local Linux input-method selection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import pocket_editor as core


class InputPolicyTests(unittest.TestCase):
    def test_auto_uses_local_compose_during_tk_start_and_restores_child_environment(self):
        captured=[];root=Mock()
        def construct():captured.append(os.environ.get('XMODIFIERS'));return root
        with patch.dict(os.environ,{'XMODIFIERS':'@im=ibus'}),patch.object(core.sys,'platform','linux'),patch.object(core.tk,'TkVersion',8.6),patch.object(core.tk,'Tk',side_effect=construct):
            self.assertIs(core.create_root(),root)
            self.assertEqual(captured,['@im=none'])
            self.assertEqual(os.environ['XMODIFIERS'],'@im=ibus')
            self.assertEqual(root.pocket_input_method,'local')

    def test_explicit_system_other_platforms_and_tk9_keep_desktop_ime(self):
        for platform,version,mode in (('linux',8.6,'system'),('linux',9.0,'auto'),('win32',8.6,'auto'),('darwin',8.6,'auto')):
            with self.subTest(platform=platform,version=version,mode=mode),patch.dict(os.environ,{'XMODIFIERS':'@im=fcitx'}),patch.object(core.sys,'platform',platform),patch.object(core.tk,'TkVersion',version):
                def construct():self.assertEqual(os.environ['XMODIFIERS'],'@im=fcitx');return Mock()
                with patch.object(core.tk,'Tk',side_effect=construct):self.assertEqual(core.create_root(mode).pocket_input_method,'system')

    def test_failed_tk_start_restores_environment_and_local_mode_removes_temporary_variable(self):
        with patch.dict(os.environ,{'XMODIFIERS':'@im=ibus'}),patch.object(core.sys,'platform','linux'),patch.object(core.tk,'Tk',side_effect=tk.TclError('test display unavailable')):
            with self.assertRaises(tk.TclError):core.create_root('local')
            self.assertEqual(os.environ['XMODIFIERS'],'@im=ibus')
        with patch.dict(os.environ,clear=True),patch.object(core.sys,'platform','linux'),patch.object(core.tk,'Tk',return_value=Mock()):
            core.create_root('local');self.assertNotIn('XMODIFIERS',os.environ)
        with self.assertRaises(ValueError):core.create_root('unsupported')

    def test_input_policy_setting_is_validated(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(core,'SETTINGS_PATH',Path(temporary)/'settings.json'):
            for value in ('system','local','auto'):
                core.SETTINGS_PATH.write_text(json.dumps({'input_method':value}))
                self.assertEqual(core.read_settings()['input_method'],value)
            core.SETTINGS_PATH.write_text('{"input_method":true}')
            self.assertEqual(core.read_settings()['input_method'],'auto')


class UndoBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root=core.create_root();self.changed=Mock()
        self.text=core.DocumentText(self.root,self.changed,undo=True)
        self.text.pack();self.root.update()
    def tearDown(self):self.root.destroy()

    def test_empty_undo_and_redo_from_tcl_after_do_not_escape_mainloop(self):
        # This pure Tcl callback previously caused mainloop() to raise, despite catch.
        script='; '.join('catch {'+str(self.text)+' edit '+operation+'}' for operation in ('undo','redo')*20)
        self.root.tk.call('after',0,script)
        self.root.after(80,self.root.quit)
        self.root.mainloop()
        self.assertEqual(self.text.get('1.0','end-1c'),'')
        self.changed.assert_not_called()

    def test_real_history_still_works_empty_history_does_not_increment_revision(self):
        self.text.insert('1.0','hello café 😀');self.text.edit_separator()
        self.text.edit_undo();self.assertEqual(self.text.get('1.0','end-1c'),'')
        calls=self.changed.call_count
        self.text.edit_undo();self.assertEqual(self.changed.call_count,calls)
        self.text.edit_redo();self.assertEqual(self.text.get('1.0','end-1c'),'hello café 😀')
        calls=self.changed.call_count
        self.text.edit_redo();self.assertEqual(self.changed.call_count,calls)
        with self.assertRaises(tk.TclError):self.text.index('not-a-valid-text-index')

    def test_invalid_tcl_text_command_is_not_silently_swallowed(self):
        with self.assertRaises(tk.TclError):self.text.tk.call(str(self.text),'edit','undo','unexpected-argument')



class NativeKeyboardTests(unittest.TestCase):
    def test_full_editor_plugins_type_and_empty_history_without_xim_warnings(self):
        if not sys.platform.startswith('linux'):self.skipTest('X11 regression is Linux-only')
        script=r"""
import json,os,tempfile,time
from pathlib import Path
import pocket_editor as core
with tempfile.TemporaryDirectory() as directory:
 core.SETTINGS_PATH=Path(directory)/'settings.json'
 root=core.create_root('auto');editor=core.Editor(root,False)
 deadline=time.monotonic()+12
 while not editor.plugins_ready and time.monotonic()<deadline:root.update();time.sleep(.01)
 assert editor.plugins_ready
 file=Path(directory)/'sample.fpf';file.write_text('')
 editor.open_path(file);text=editor.buffer;text.focus_force();root.update()
 for key in ('a','b','c')*5:
  text.event_generate('<KeyPress>',keysym=key);text.event_generate('<KeyRelease>',keysym=key);root.update()
 assert editor.buffer.get('1.0','end-1c')=='abc'*5
 name=str(editor.buffer)
 root.tk.call('after',0,'catch {'+name+' edit reset}; catch {'+name+' edit undo}; catch {'+name+' edit redo}')
 root.after(150,root.quit);root.mainloop()
 print(json.dumps({'mode':root.pocket_input_method,'environment':os.environ.get('XMODIFIERS'),'plugins':len(editor.plugin_apis)}))
 editor.buffer.edit_modified(False);editor.rich_dirty=False;editor.quit()
"""
        env=dict(os.environ,XMODIFIERS='@im=ibus',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(core.__file__).parent,env=env,capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('fabricat',result.stderr.lower());self.assertNotIn('Traceback',result.stderr)
        data=json.loads(result.stdout);self.assertEqual(data['mode'],'local');self.assertEqual(data['environment'],'@im=ibus')
        self.assertGreater(data['plugins'],10)

    def test_auto_linux_start_types_and_changes_focus_without_xim_warnings(self):
        if not sys.platform.startswith('linux'):self.skipTest('X11 regression is Linux-only')
        script=r'''
import json,os,tkinter as tk
import pocket_editor as core
root=core.create_root('auto')
text=core.DocumentText(root,lambda:None,undo=True);text.pack();text.focus_force();root.update()
for index in range(24):
 text.event_generate('<KeyPress>',keysym='a');text.event_generate('<KeyRelease>',keysym='a')
 root.update()
 if index%4==0:
  dialog=tk.Toplevel(root);entry=tk.Entry(dialog);entry.pack();entry.focus_force();root.update()
  dialog.destroy();text.focus_force();root.update()
root.tk.call('after',0,'catch {'+str(text)+' edit reset}; catch {'+str(text)+' edit undo}; catch {'+str(text)+' edit redo}')
root.after(100,root.quit);root.mainloop()
print(json.dumps({'text':text.get('1.0','end-1c'),'environment':os.environ.get('XMODIFIERS'),'mode':root.pocket_input_method,'xim':root.tk.call('tk','useinputmethods')}))
root.destroy()
'''
        env=dict(os.environ,XMODIFIERS='@im=ibus',PYTHONDONTWRITEBYTECODE='1')
        run=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(core.__file__).parent,env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertNotIn('fabricat',run.stderr.lower())
        data=json.loads(run.stdout)
        self.assertEqual(data['text'],'a'*24)
        self.assertEqual(data['environment'],'@im=ibus')
        self.assertEqual(data['mode'],'local')
        self.assertTrue(int(data['xim']))  # XIM remains enabled for local compose.


class KeyboardPreferencesTests(unittest.TestCase):
    def test_system_input_preference_is_saved_without_changing_running_keyboard(self):
        import test_editor as fixtures
        case=fixtures.EditorTests('test_settings_validation_and_font_isolation')
        case.setUp()
        try:
            editor=case.editor;editor.preferences();case.pump()
            window=editor.settings_window
            from tkinter import ttk
            combo=next(widget for widget in fixtures.widgets(window) if isinstance(widget,ttk.Combobox) and tuple(widget.cget('values'))==('auto','system','local'))
            before=case.root.tk.call('tk','useinputmethods')
            combo.set('system')
            apply=next(widget for widget in fixtures.widgets(window) if isinstance(widget,ttk.Button) and widget.cget('text')=='Apply')
            apply.invoke();case.pump()
            self.assertEqual(core.read_settings()['input_method'],'system')
            self.assertEqual(editor.input_method,'system')
            self.assertEqual(case.root.tk.call('tk','useinputmethods'),before)
        finally:case.tearDown()
