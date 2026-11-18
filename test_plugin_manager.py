"""Plugin manager persistence, errors, safe mode, and communication inspection."""
import json
import tkinter as tk
import unittest
from unittest.mock import patch

import pocket_editor as core
import test_command_palette as fixtures


class PluginManagerTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown

    def test_disable_persistence_and_inspect_loaded_plugin(self):
        path = core.PLUGIN_DIR / 'sample.py'
        path.write_text('def register(api):\n api.add_command("Sample", lambda: None)\n api.on("custom.event", lambda **payload: None)\n api.provide_service("ping", lambda: "pong")\n')
        self.editor.load_plugins()
        manager = self.editor.plugin_manager; manager.show(); self.root.update()
        manager.tree.selection_set('sample'); manager.inspect()
        details = manager.details.get('1.0', 'end')
        self.assertIn('Sample', details); self.assertIn('custom.event', details); self.assertIn('sample.ping', details)
        manager.toggle()
        self.assertEqual(core.read_settings()['disabled_plugins'], ['sample'])
        self.assertIn('after restart', manager.status('sample', path))
        self.assertEqual(self.editor.plugin_apis['sample'].call_service('sample.ping'), 'pong')
        manager.open_source(); self.assertEqual(self.editor.path, path)
        other_root = tk.Toplevel(self.root)
        other = core.Editor(other_root, False); self.root.update()
        self.assertNotIn('sample', other.plugin_apis)
        other.quit()
        manager.toggle()
        self.assertEqual(core.read_settings()['disabled_plugins'], [])

    def test_load_errors_retry_and_failed_settings_rollback(self):
        path = core.PLUGIN_DIR / 'broken.py'
        path.write_text('def register(api):\n raise ValueError("broken registration")\n')
        with patch.object(core.messagebox, 'showerror'):
            self.editor.load_plugins()
        manager = self.editor.plugin_manager; manager.show(); self.root.update()
        self.assertIn('broken registration', manager.details.get('1.0', 'end'))
        with patch.object(core.messagebox, 'showerror'), patch.object(core.os, 'replace', side_effect=OSError('read only')):
            manager.toggle()
        self.assertNotIn('broken', self.editor.disabled_plugins)
        path.write_text('def register(api):\n api.add_command("Fixed", lambda: None)\n')
        manager.load_new()
        self.assertIn('broken', self.editor.plugin_apis)
        self.assertNotIn('broken', self.editor.plugin_errors)
        with patch.object(core.messagebox, 'showerror'):
            self.editor.run_plugin('broken.py', lambda: 1 / 0)
        manager.refresh()
        self.assertIn('ZeroDivisionError', manager.details.get('1.0', 'end'))
        manager.query.set('does-not-exist')
        self.assertEqual(manager.tree.get_children(), ())

    def test_safe_mode_manager_and_disabled_settings_validation(self):
        path = core.PLUGIN_DIR / 'sample.py'; path.write_text('raise RuntimeError("must not run")')
        other_root = tk.Toplevel(self.root)
        other = core.Editor(other_root, True); self.root.update()
        other.plugin_manager.show(); self.root.update()
        self.assertIn('safe mode', other.plugin_manager.status('sample', path))
        other.load_plugins()
        self.assertFalse(other.plugin_apis)
        other.plugin_manager.toggle()
        self.assertIn('sample', core.read_settings()['disabled_plugins'])
        other.quit()
        core.SETTINGS_PATH.write_text(json.dumps({'disabled_plugins': ['../escape']}))
        self.assertEqual(core.read_settings()['disabled_plugins'], [])
