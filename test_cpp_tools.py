"""C/C++ backend and real Tk regressions; optional real LLVM/GDB integration."""
import copy
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pocket_editor_plugins import _cpp_language as language
from pocket_editor_plugins._cpp_debug import Debugger, mi_fields, mi_string
import test_editor as fixtures


class BackendTests(unittest.TestCase):
    def test_settings_validation_and_lazy_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'settings.json'
            path.write_text('{"automatic": "yes", "delay_ms": true, "style": "bad", "includes": [4,"inc"]}')
            settings=language.read_settings(path)
            self.assertTrue(settings['automatic']);self.assertEqual(settings['delay_ms'],250)
            self.assertEqual(settings['style'],'file');self.assertEqual(settings['includes'],['inc'])
        worker=language.ToolWorker();self.assertIsNone(worker.thread);worker.close()
        self.assertTrue(language.is_cpp(Path('x.C')));self.assertEqual(language.language(Path('x.C')),'cpp')

    def test_debugger_parser_nested_records_and_escapes(self):
        result=mi_fields('reason="breakpoint-hit",frame={func="main",line="4"},stack=[frame={level="0",func="main"},frame={level="1",func="entry"}],variables=[{name="s",value="a\\n\\\"b"}]')
        self.assertEqual(result['frame']['line'],'4');self.assertEqual(result['stack'][1]['frame']['func'],'entry')
        self.assertEqual(result['variables'][0]['value'],'a\n"b')
        self.assertEqual(mi_string('"a\\033b"')[0],'a\x1bb')
        for text in ('x={name="unterminated}', 'x=[{name="x"}', 'x='):
            with self.assertRaises(ValueError):mi_fields(text)

    @unittest.skipUnless(shutil.which('g++'),'G++ is required for compiler integration')
    def test_real_compiler_checks_unsaved_source_and_builds_debug_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'sample.cpp';path.write_text('int main(){ return 0; }\n')
            settings=copy.deepcopy(language.DEFAULTS)
            errors=language.check_source('int main(){ missing_name = 1; }\n',path,settings,root)
            self.assertTrue(any(item['severity']=='error' and 'missing_name' in item['message'] for item in errors))
            self.assertEqual(path.read_text(),'int main(){ return 0; }\n')
            self.assertEqual(language.check_source(path.read_text(),path,settings,root),[])
            target=language.build_source(path,settings,root)
            self.assertTrue(Path(target).is_file())
            self.assertEqual(subprocess.run([target],capture_output=True,timeout=5).returncode,0)

    def test_process_timeout_and_cancellation_reap_owned_process(self):
        import sys
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            language.run_process([sys.executable,'-c','import time; time.sleep(30)'],cancelled=lambda:True)


class CppGuiTests(unittest.TestCase):
    setUp=fixtures.EditorTests.setUp
    tearDown=fixtures.EditorTests.tearDown
    pump=fixtures.EditorTests.pump
    until=fixtures.EditorTests.until

    def app(self):
        return next(callback.__self__ for label,callback in self.editor.plugin_apis['cpp_tools']._commands if label=='C/C++ Tools')

    def open_source(self,text='int main(){return 0;}\n'):
        path=self.folder/'main.cpp';path.write_text(text);self.editor.open_path(path);self.pump()
        app=self.app();app.settings['automatic']=False;app.settings['syntax_automatic']=False;app.cancel_timer('pending_syntax')
        return app,path

    def test_formatting_is_undoable_and_stale_results_do_not_replace_edits(self):
        app,path=self.open_source();source=app.api.get_text();formatted='int main() { return 0; }\n'
        with patch.object(language,'format_source',return_value=formatted):
            app.format_document();self.until(lambda:not app.jobs)
        self.assertEqual(app.api.get_text(),formatted);self.assertEqual(path.read_text(),source)
        self.editor.buffer.edit_undo();self.assertEqual(app.api.get_text(),source)
        started=threading.Event();release=threading.Event()
        def format_source(*args):started.set();release.wait(3);return formatted
        with patch.object(language,'format_source',side_effect=format_source):
            app.format_document();self.until(started.is_set)
            self.editor.buffer.insert('end-1c','// newer edit\n');release.set();self.until(lambda:not app.jobs)
        self.assertIn('// newer edit',app.api.get_text())

    def test_completion_inserts_snippet_and_rejects_outdated_capture(self):
        app,path=self.open_source('int main(){ ret }\n')
        self.editor.buffer.mark_set('insert','1.15')
        capture=app.capture()
        item={'label':'return','insertText':'return ${1:0};$0','insertTextFormat':2}
        app.show_suggestions([item],capture);self.root.update()
        self.assertTrue(app.popup.winfo_viewable())
        app.accept();self.assertIn('return 0;',app.api.get_text())
        self.editor.buffer.edit_undo();self.assertEqual(app.api.get_text(),capture[1])
        app.show_suggestions([item],app.capture());self.editor.buffer.insert('end-1c','// change')
        app.accept();self.assertIn('// change',app.api.get_text());self.assertNotIn('return',app.api.get_text())

    @unittest.skipUnless(shutil.which('g++'),'G++ is required for syntax integration')
    def test_red_zigzags_error_list_and_stale_diagnostic_cleanup(self):
        app,path=self.open_source('int main(){return 0;}\n');app.show();self.until(lambda:app.window is not None and 'cpp_tools' not in self.editor.plugin_sidebar.pending)
        self.editor.buffer.delete('1.0','end');self.editor.buffer.insert('1.0','int main(){ missing_name = 1; }\n')
        app.check_syntax();self.until(lambda:not app.jobs);self.root.update();app.paint_signature=None;app.paint_squiggles()
        self.assertTrue(app.diagnostics);self.assertTrue(app.issues.get_children());self.assertTrue(app.squiggles)
        canvas=app.squiggles[0];line=canvas.find_all()[0]
        self.assertEqual(canvas.itemcget(line,'fill'),'#ff5252')
        self.assertEqual(set(canvas.coords(line)[1::2]),{1.0,3.0})
        self.editor.buffer.insert('end-1c','// modified');self.pump(.2)
        self.assertEqual(app.squiggles,[]);self.assertEqual(app.diagnostics,[])
        self.assertEqual(path.read_text(),'int main(){return 0;}\n')

    def test_format_error_is_visible_and_loading_token_is_released(self):
        app,path=self.open_source();before=app.api.get_text()
        with patch.object(language,'format_source',side_effect=RuntimeError('Formatter unavailable')):
            app.format_document();self.until(lambda:not app.jobs)
        self.assertEqual(app.api.get_text(),before)
        self.showerror.assert_called()
        self.until(lambda:not app.api.call_service('loading_indicator.status'))

    def test_menu_and_vertical_tab_reuse_one_embedded_panel_and_debugger_tab(self):
        app,path=self.open_source();sidebar=self.editor.plugin_sidebar
        self.editor.run_plugin('cpp_tools.py',app.show_debugger)
        self.until(lambda:app.window is not None and 'cpp_tools' not in sidebar.pending)
        panel=app.window
        self.assertTrue(getattr(panel,'_docked_panel',False))
        self.assertIs(panel.master,sidebar.hosts['cpp_tools'])
        self.assertEqual(app.notebook.index(app.notebook.select()),1)
        self.assertFalse(any(widget.winfo_class()=='Toplevel' and widget.title()=='C/C++ Tools'
                             for widget in fixtures.widgets(self.root)))
        app.program.set('/keep/program');app.watch.set('counter.value')
        for _ in range(3):
            sidebar.toggle('cpp_tools');self.assertFalse(sidebar.visible)
            app.show();self.pump();self.assertTrue(sidebar.visible)
            self.assertIs(app.window,panel);self.assertTrue(panel.winfo_ismapped())
            sidebar.open('git_project');self.pump(.2)
            sidebar.open('cpp_tools');self.pump()
            self.assertIs(app.window,panel)
        self.assertEqual(app.program.get(),'/keep/program');self.assertEqual(app.watch.get(),'counter.value')

    def test_result_rendering_keeps_loading_active_until_display_handoff(self):
        app,path=self.open_source();app.show()
        self.until(lambda:app.window is not None and 'cpp_tools' not in self.editor.plugin_sidebar.pending)
        self.until(lambda:not app.api.call_service('loading_indicator.status'))
        capture=app.capture();seen=[]
        def render(items,result_capture):
            seen.append(bool(app.api.call_service('loading_indicator.status')))
            original(items,result_capture)
        original=app.show_diagnostics
        with patch.object(language,'check_source',return_value=[]),patch.object(app,'show_diagnostics',side_effect=render):
            app.check_syntax();self.until(lambda:not app.jobs)
        self.assertEqual(seen,[True])
        self.until(lambda:not app.api.call_service('loading_indicator.status'))

    def test_panel_settings_footer_and_breakpoint_toggle(self):
        app,path=self.open_source();self.editor.plugin_sidebar.open('cpp_tools');self.pump(.3)
        self.assertTrue(getattr(app.window,'_docked_panel',False))
        app.toggle_breakpoint();self.assertEqual(len(app.breakpoints),1)
        app.toggle_breakpoint();self.assertFalse(app.breakpoints)
        app.preferences();app.settings_window.geometry('420x360');self.root.update()
        save=next(widget for widget in fixtures.widgets(app.settings_window) if hasattr(widget,'cget') and widget.winfo_class()=='TButton' and widget.cget('text')=='Save Settings')
        self.assertTrue(save.winfo_ismapped());self.assertLess(save.winfo_rooty()+save.winfo_height(),app.settings_window.winfo_rooty()+app.settings_window.winfo_height()+1)

    @unittest.skipUnless(os.environ.get('CPP_TEST_TOOLS'),'Requires private GDB validation tools')
    def test_debugger_gui_receives_real_breakpoints_variables_and_cleans_up(self):
        app,path=self.open_source('int main() {\n int value = 41;\n value += 1;\n return value == 42 ? 0 : 1;\n}\n')
        root=Path(os.environ['CPP_TEST_TOOLS']);app.settings['gdb']=str(root/'usr/bin/gdb')
        target=language.build_source(path,app.settings,self.folder)
        app.program.set(target);self.editor.buffer.mark_set('insert','3.0');app.toggle_breakpoint()
        app.start_debug();debugger=app.debugger
        try:
            self.until(lambda:app.debug_stopped,15)
            self.until(lambda:app.variables is not None and bool(app.variables.get_children()))
            values=[app.variables.item(row,'values') for row in app.variables.get_children()]
            self.assertIn(('value','41'),values)
            self.assertTrue(app.stack.get_children())
            app.watch.set('value');app.debug_command('watch','value')
            self.until(lambda:app.status.get()=='value = 41')
            app.debug_command('next');self.until(lambda:self.editor.buffer.index('insert')=='4.0')
        finally:
            app.stop_debug();self.until(lambda:app.debugger is None,6)
            debugger.thread.join(timeout=3)
        self.assertFalse(debugger.thread.is_alive());self.assertIsNotNone(debugger.process.poll())


class RealToolsTests(unittest.TestCase):
    def tools(self):
        root=os.environ.get('CPP_TEST_TOOLS')
        if not root:self.skipTest('Set CPP_TEST_TOOLS to a private LLVM/GDB tools root, or install tools first.')
        root=Path(root);settings=copy.deepcopy(language.DEFAULTS)
        settings.update(clang_format=str(root/'usr/bin/clang-format-19'),clangd=str(root/'usr/bin/clangd-19'),gdb=str(root/'usr/bin/gdb'))
        return settings

    def test_real_clang_format_and_clangd_unsaved_completions(self):
        settings=self.tools()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'main.cpp';path.write_text('int main(){return 0;}\n')
            formatted=language.format_source(path.read_text(),path,settings)
            self.assertIn('int main() {',formatted);self.assertEqual(path.read_text(),'int main(){return 0;}\n')
            source='struct Example { int member_value; };\nint main(){ Example object; object. }\n'
            server=language.Clangd(root,settings,path=path)
            try:
                items=server.complete(path,source,source.index('object. }')+len('object.'))
                self.assertTrue(any('member_value' in item['label'] for item in items),items)
                source2=source.replace('member_value','other_value')
                items=server.complete(path,source2,source2.index('object. }')+len('object.'))
                self.assertTrue(any('other_value' in item['label'] for item in items),items)
            finally:server.close()
            self.assertIsNotNone(server.process.poll())

    def test_real_gdb_breakpoint_step_variables_watch_and_stop(self):
        settings=self.tools()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'debug.cpp'
            path.write_text('int main() {\n int value = 41;\n value += 1;\n return value == 42 ? 0 : 1;\n}\n')
            target=language.build_source(path,settings,root)
            debugger=Debugger(target,[],root,settings,[str(path)+':3'])
            def wait(kind,timeout=15):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    try:event,value=debugger.events.get(timeout=.1)
                    except queue.Empty:continue
                    if event=='error':self.fail(value)
                    if event==kind:return value
                self.fail('No debugger event: '+kind)
            try:
                stopped=wait('stopped');self.assertEqual(stopped.get('reason'),'breakpoint-hit')
                variables=wait('variables');self.assertTrue(any(item.get('name')=='value' and item.get('value')=='41' for item in variables),variables)
                debugger.submit('watch','value');self.assertEqual(wait('watch'),('value','41'))
                debugger.submit('next');self.assertEqual(wait('stopped').get('frame',{}).get('line'),'4')
                debugger.submit('continue');self.assertEqual(wait('stopped').get('reason'),'exited-normally')
            finally:
                debugger.close();debugger.thread.join(timeout=5)
                self.assertFalse(debugger.thread.is_alive())
                self.assertIsNotNone(debugger.process.poll())
