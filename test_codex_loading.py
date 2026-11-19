"""Offline Codex/loader tests; no real model requests or API credits."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins._codex_cli import CodexRunner, command
from pocket_editor_plugins.codex_assistant import CodexAssistant
from pocket_editor_plugins.loading_indicator import LoadingIndicator


class CodexRunnerTests(unittest.TestCase):
    def test_command_defaults_to_read_only_and_uses_stdin(self):
        args = command(['codex'], '/project with spaces', '/reply', model='custom-model')
        self.assertEqual(args[args.index('--sandbox')+1], 'read-only')
        self.assertEqual(args[-1], '-')
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
        edit_args = command(['codex'], '/p', '/o', True)
        self.assertEqual(edit_args[edit_args.index('--sandbox')+1], 'workspace-write')

    def test_fake_cli_roundtrip_and_process_group_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / 'fake_codex.py'
            fake.write_text("import sys,time\nfrom pathlib import Path\nprompt=sys.stdin.read()\nif prompt=='wait': time.sleep(30)\nPath(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('Reply: '+prompt)\n")
            runner = CodexRunner()
            with patch('pocket_editor_plugins._codex_cli.cli_prefix', return_value=[sys.executable, str(fake)]):
                self.assertEqual(runner.run('',temp,'hello'), 'Reply: hello')
                errors=[]
                def work():
                    try: runner.run('',temp,'wait')
                    except RuntimeError as error: errors.append(str(error))
                thread=threading.Thread(target=work); thread.start()
                deadline=time.monotonic()+4
                while runner.process is None and time.monotonic()<deadline: time.sleep(.01)
                self.assertIsNotNone(runner.process)
                runner.cancel();thread.join(timeout=12)
                self.assertFalse(thread.is_alive());self.assertIn('stopped',errors[0])
            runner.close()


class LoadingGuiTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown

    def hub(self):
        api=core.PluginAPI(self.editor,'loading_indicator')
        hub=LoadingIndicator(api);self.editor.plugin_apis['loading_indicator']=api
        return api,hub

    def test_concurrent_jobs_progress_and_parent_destroy_cleanup(self):
        api,hub=self.hub()
        first=api.call_service('loading_indicator.start',message='First job')
        holder=tk.Frame(self.root);holder.pack()
        second=api.call_service('loading_indicator.start',message='Second job',parent=holder)
        self.root.update()
        self.assertEqual(len(hub.jobs),2)
        self.assertTrue(api.call_service('loading_indicator.update',first,progress=.5))
        self.assertEqual(hub.jobs[first]['progress'],.5)
        with self.assertRaises(ValueError):hub.update(first,progress=2)
        hub.stop(first);self.assertIn(second,hub.jobs)
        holder.destroy()
        deadline=time.monotonic()+.15
        while time.monotonic()<deadline:self.root.update();time.sleep(.01)
        self.assertEqual(hub.jobs,{})
        self.assertFalse(hub.stop(first))
        hub.cleanup();self.assertIsNone(hub.timer)

    def test_codex_gui_uses_loading_and_keeps_requests_explicit(self):
        _,hub=self.hub()
        api=core.PluginAPI(self.editor,'codex_assistant')
        app=CodexAssistant(api);self.editor.plugin_apis['codex_assistant']=api
        app.show();app.folder.set(self.temp.name)
        self.assertFalse(app.edits.get());self.assertFalse(app.attach.get())
        app.prompt.insert('1.0','Explain this project')
        with patch.object(app.runner,'run',return_value='A project explanation') as run:
            app.submit();self.assertEqual(len(hub.jobs),1)
            deadline=time.monotonic()+4
            while app.busy and time.monotonic()<deadline:self.root.update();time.sleep(.01)
        self.assertFalse(app.busy);self.assertFalse(hub.jobs)
        self.assertFalse(run.call_args.args[3])
        self.assertEqual(app.last_reply,'A project explanation')
        app.help();self.root.update()
        self.editor.set_document(Path(self.temp.name)/'open.py','unsaved code')
        app.edits.set(True)
        with patch.object(app.runner,'run') as run, patch('tkinter.messagebox.showinfo'):
            app.submit();run.assert_not_called()
