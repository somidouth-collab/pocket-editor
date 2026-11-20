"""Real Tk tests for the command palette and live menu discovery."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import pocket_editor as core
from command_palette import filter_commands, menu_commands


class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        self.patches = [patch.object(core, 'PLUGIN_DIR', folder),
                        patch.object(core, 'SETTINGS_PATH', folder / 'settings.json'),
                        patch.object(core.messagebox, 'askyesnocancel', return_value=False)]
        for item in self.patches:
            item.start()
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.editor = core.Editor(self.root, False)
        self.root.update()
        self.palette = self.editor.command_palette

    def tearDown(self):
        try:
            self.editor.quit()
            self.assertEqual(self.errors, [])
        finally:
            for item in reversed(self.patches):
                item.stop()
            self.temp.cleanup()

    def test_filter_navigation_empty_and_escape(self):
        self.palette.show(); self.root.update()
        window = self.palette.window
        self.palette.show()
        self.assertIs(self.palette.window, window)
        self.palette.query.set('FILE SAVE')
        self.assertGreaterEqual(len(self.palette.matches), 3)
        self.assertTrue(all('Save' in command[0] for command in self.palette.matches))
        self.palette.entry.event_generate('<Down>'); self.root.update()
        self.assertEqual(self.palette.results.curselection(), (1,))
        self.palette.entry.event_generate('<Up>'); self.root.update()
        self.assertEqual(self.palette.results.curselection(), (0,))
        self.palette.query.set('nothing-matches-123')
        self.assertEqual(self.palette.matches, [])
        self.palette.run()
        self.assertTrue(window.winfo_exists())
        self.palette.window.event_generate('<Escape>'); self.root.update()
        self.assertIsNone(self.palette.window)

    def test_live_plugin_command_and_disabled_exclusion(self):
        path = core.PLUGIN_DIR / 'example.py'
        path.write_text('def register(api):\n    api.add_command("Test Palette Plugin", lambda: api.replace_text("plugin ran"))\n')
        self.editor.load_plugins()
        self.editor.plugins.add_command(label='Disabled example', state='disabled', command=lambda: None)
        self.palette.show(); self.root.update()
        self.palette.query.set('test palette plugin')
        self.assertEqual(len(self.palette.matches), 1)
        self.assertEqual(self.palette.matches[0][0], 'Plugins › Test Palette Plugin')
        self.palette.run(); self.root.update()
        self.assertIsNone(self.palette.window)
        self.assertEqual(self.editor.buffer.get('1.0', 'end-1c'), 'plugin ran')
        self.editor.buffer.edit_undo()
        self.assertEqual(self.editor.buffer.get('1.0', 'end-1c'), '')
        self.assertFalse(any('Disabled example' in item[0] for item in self.palette.commands))

    def test_menu_toggles_and_safe_mode(self):
        self.palette.show(); self.root.update()
        original = self.editor.word_wrap.get()
        self.palette.query.set('word wrap')
        self.palette.run(); self.root.update()
        self.assertEqual(self.editor.word_wrap.get(), not original)
        self.assertEqual(core.read_settings()['word_wrap'], not original)
        other = tk.Toplevel(self.root)
        safe = core.Editor(other, True)
        self.root.update()
        safe.command_palette.show(); self.root.update()
        self.assertTrue(safe.command_palette.matches)
        self.assertTrue(all('Plugin Manager' in item[0] for item in safe.command_palette.matches if item[0].startswith('Plugins ›')))
        safe.quit()

    def test_shortcut_and_core_new_command(self):
        self.editor.buffer.focus_force(); self.root.update()
        self.editor.buffer.event_generate('<Control-Shift-P>'); self.root.update()
        self.assertIsNotNone(self.palette.window)
        self.palette.query.set('file new')
        self.palette.entry.event_generate('<Return>'); self.root.update()
        self.assertEqual(len(self.editor.documents), 2)
        self.assertIsNone(self.palette.window)

    def test_filter_unicode_and_prefix_ranking(self):
        commands = [('File › Save As', '', None), ('File › Save', '', None), ('Plugins › Café', '', None)]
        self.assertEqual(filter_commands(commands, ' CAFÉ ')[0][0], 'Plugins › Café')
        self.assertEqual(filter_commands(commands, 'save')[0][0], 'File › Save')


if __name__ == '__main__':
    unittest.main()
