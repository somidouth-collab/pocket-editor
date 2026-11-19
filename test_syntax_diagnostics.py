"""Non-executing syntax checks and stale-result protection in real Tk."""
import time
import unittest

import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins._python_diagnostics import Diagnostics, check_syntax


class ParserTests(unittest.TestCase):
    def test_python_json_scope_and_size(self):
        self.assertEqual(check_syntax('raise RuntimeError("must not execute")', '.py')['status'], 'ok')
        self.assertEqual(check_syntax('return 1', '.py')['status'], 'error')
        error = check_syntax('x = 1\nif True\n    pass', '.py')
        self.assertEqual(error['line'], 2)
        self.assertEqual(check_syntax('{"valid": true}', '.json')['status'], 'ok')
        self.assertEqual(check_syntax('{"bad": }', '.json')['status'], 'error')
        self.assertEqual(check_syntax('anything', '.txt')['status'], 'unsupported')
        self.assertEqual(check_syntax('x' * 200001, '.py')['status'], 'skipped')


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.api = core.PluginAPI(self.editor, 'syntax_diagnostics')
        self.diagnostics = Diagnostics(self.api)
        self.editor.plugin_apis['syntax_diagnostics'] = self.api

    tearDown = fixtures.PaletteTests.tearDown

    def wait_result(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.root.update()
            if self.diagnostics.result is not None and not self.diagnostics.busy:
                return
            time.sleep(.02)
        self.fail('Syntax check did not finish')

    def test_error_marker_jump_fix_and_undo(self):
        e = self.editor
        e.set_document(core.PLUGIN_DIR / 'sample.py', 'x = 1\nif True\n    pass')
        received = []
        self.api.on('syntax_diagnostics.updated', lambda **payload: received.append(payload))
        self.api.call_service('syntax_diagnostics.check'); self.wait_result()
        self.root.update()
        self.assertEqual(received[-1]['result']['status'], 'error')
        self.assertEqual(self.api.call_service('syntax_diagnostics.get_status')['result']['status'], 'error')
        self.assertEqual(self.diagnostics.result['status'], 'error')
        self.assertTrue(e.buffer.tag_ranges('diagnostic_error'))
        self.diagnostics.jump()
        self.assertTrue(e.buffer.index('insert').startswith('2.'))
        e.buffer.edit_separator()
        e.buffer.insert('2.end', ':')
        e.buffer.edit_separator()
        self.root.update()
        self.diagnostics.request(manual=True); self.wait_result()
        self.assertEqual(self.diagnostics.result['status'], 'ok')
        self.assertFalse(e.buffer.tag_ranges('diagnostic_error'))
        e.buffer.edit_undo()
        self.assertIn('if True\n', self.api.get_text())

    def test_stale_results_tab_switch_and_automatic_toggle(self):
        e = self.editor
        e.set_document(core.PLUGIN_DIR / 'bad.json', '{"bad": }')
        self.diagnostics.request(manual=True)
        old_identity = self.diagnostics.identity()
        e.new(); e.set_document(core.PLUGIN_DIR / 'good.py', 'print("ok")')
        self.root.update()
        self.root.after_cancel(self.diagnostics.poll_job)
        self.diagnostics.replace_queue(self.diagnostics.results, (old_identity, {'status': 'error', 'message': 'stale', 'line': 1, 'column': 1}, True))
        self.diagnostics.poll()
        self.assertFalse(e.buffer.tag_ranges('diagnostic_error'))
        self.diagnostics.request(manual=True); self.wait_result()
        self.assertEqual(self.diagnostics.result['status'], 'ok')
        self.diagnostics.toggle()
        self.assertFalse(self.diagnostics.automatic)
        e.buffer.insert('end-1c', '\nreturn')
        self.root.update()
        self.assertIsNone(self.diagnostics.result)
        self.diagnostics.request(manual=True); self.wait_result()
        self.assertEqual(self.diagnostics.result['status'], 'error')
