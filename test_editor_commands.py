"""Tests for everyday palette commands, including undo and canceled tab closes."""
import unittest
from unittest.mock import patch

import test_command_palette as palette_tests
import pocket_editor as core
from pocket_editor_plugins import editor_commands as commands


class EditorCommandTests(unittest.TestCase):
    tearDown = palette_tests.PaletteTests.tearDown
    # Reuse the isolated GUI fixture; avoid inheriting palette test methods below.
    def setUp(self):
        palette_tests.PaletteTests.setUp(self)
        self.api = core.PluginAPI(self.editor, 'editor_commands')
        commands.register(self.api)
        self.actions = dict(self.api._commands)

    def test_line_edits_undo_and_selection_boundaries(self):
        e = self.editor
        for operation, expected in (('duplicate', 'one\ntwo\ntwo\nthree'),
                                    ('up', 'two\none\nthree'),
                                    ('down', 'one\nthree\ntwo'),
                                    ('delete', 'one\nthree')):
            with self.subTest(operation=operation):
                e.set_document(None, 'one\ntwo\nthree')
                e.buffer.mark_set('insert', '2.1')
                commands.edit_lines(e, operation)
                self.assertEqual(self.api.get_text(), expected)
                e.buffer.edit_undo()
                self.assertEqual(self.api.get_text(), 'one\ntwo\nthree')
        e.set_document(None, 'one\ntwo\nthree\n')
        e.buffer.tag_add('sel', '1.0', '3.0')
        commands.edit_lines(e, 'duplicate')
        self.assertEqual(self.api.get_text(), 'one\ntwo\none\ntwo\nthree\n')
        e.set_document(None, 'only')
        commands.edit_lines(e, 'up'); commands.edit_lines(e, 'down')
        self.assertEqual(self.api.get_text(), 'only')
        commands.edit_lines(e, 'delete')
        self.assertEqual(self.api.get_text(), '')
        e.buffer.edit_undo()
        self.assertEqual(self.api.get_text(), 'only')

    def test_trim_jump_clipboard_and_zoom(self):
        e = self.editor
        e.set_document(core.PLUGIN_DIR / 'sample.txt', 'one \t\ntwo  \n')
        self.actions['Trim Trailing Whitespace']()
        self.assertEqual(self.api.get_text(), 'one\ntwo\n')
        e.buffer.edit_undo()
        self.assertEqual(self.api.get_text(), 'one \t\ntwo  \n')
        with patch.object(commands.simpledialog, 'askinteger', return_value=2):
            self.actions['Go to Line…']()
        self.assertEqual(e.buffer.index('insert'), '2.0')
        self.actions['Copy File Path']()
        self.assertEqual(self.root.clipboard_get(), str(e.path))
        self.actions['Copy Folder Path']()
        self.assertEqual(self.root.clipboard_get(), str(e.path.parent))
        original = e.editor_font.cget('size')
        self.actions['Zoom In'](); self.assertEqual(e.editor_font.cget('size'), original + 1)
        self.actions['Zoom Out'](); self.assertEqual(e.editor_font.cget('size'), original)
        self.actions['Reset Zoom'](); self.assertEqual(e.editor_font.cget('size'), 11)

    def test_tab_navigation_and_close_others_cancel(self):
        e = self.editor
        first = e.active_document
        e.buffer.insert('1.0', 'unsaved')
        e.new(); second = e.active_document
        self.actions['Previous Tab'](); self.assertIs(e.active_document, first)
        self.actions['Next Tab'](); self.assertIs(e.active_document, second)
        with patch.object(core.messagebox, 'askyesnocancel', return_value=None):
            self.actions['Close Other Tabs']()
        self.assertEqual(len(e.documents), 2)
        self.assertIs(e.active_document, second)
        self.actions['Close Other Tabs']()
        self.assertEqual(e.documents, [second])

