"""Editable split panes, active-document routing, scrolling, and close behavior."""
import unittest
from unittest.mock import patch

import pocket_editor as core
import test_command_palette as fixtures


class SplitEditorTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown

    def assert_uncovered(self, widget):
        self.root.update()
        self.assertTrue(widget.winfo_ismapped())
        self.assertIs(self.root.winfo_containing(widget.winfo_rootx() + min(8, widget.winfo_width() // 2),
                                               widget.winfo_rooty() + min(8, widget.winfo_height() // 2)), widget)

    def test_first_document_is_visible_above_pane_container(self):
        e = self.editor
        first = e.active_document
        e.set_document(None, 'First document stays visible')
        self.assert_uncovered(first['widget'])
        self.assert_uncovered(e.gutter)
        self.assert_uncovered(e.vertical_scrollbar)
        self.assert_uncovered(e.horizontal_scrollbar)
        e.new(); e.set_document(None, 'Second document')
        e.select_document(first)
        self.assert_uncovered(first['widget'])
        e.split_editor.enable()
        for document in e.split_editor.documents:
            self.assert_uncovered(document['widget'])
        e.select_document(first)
        e.split_editor.close()
        self.assert_uncovered(first['widget'])
        self.assertEqual(e.buffer.get('1.0', 'end-1c'), 'First document stays visible')

    def test_two_files_focus_save_and_independent_undo(self):
        first = core.PLUGIN_DIR / 'left.txt'; first.write_text('left')
        second = core.PLUGIN_DIR / 'right.txt'; second.write_text('right')
        e = self.editor
        e.open_path(first); left = e.active_document
        e.open_path(second); right = e.active_document
        e.select_document(left)
        e.split_editor.enable(); self.root.update()
        self.assertTrue(e.split_editor.enabled)
        self.assertTrue(left['widget'].winfo_ismapped()); self.assertTrue(right['widget'].winfo_ismapped())
        self.assertLess(left['widget'].winfo_rootx(), right['widget'].winfo_rootx())
        right['widget'].focus_force(); self.root.update()
        self.assertIs(e.active_document, right)
        api = core.PluginAPI(e, 'test')
        api.replace_text('edited right'); self.assertTrue(e.save())
        self.assertEqual(second.read_text(), 'edited right')
        self.assertEqual(first.read_text(), 'left')
        left['widget'].focus_force(); self.root.update()
        self.assertEqual(api.get_text(), 'left')
        api.replace_text('edited left')
        e.buffer.edit_undo(); self.assertEqual(api.get_text(), 'left')
        e.split_editor.other(); self.root.update()
        self.assertIs(e.active_document, right)
        self.assertEqual(api.get_text(), 'edited right')
        e.split_editor.close(); self.root.update()
        self.assertFalse(e.split_editor.enabled)
        self.assertEqual(len(e.documents), 2)
        self.assertTrue(right['widget'].winfo_ismapped())
        self.assertFalse(left['widget'].winfo_ismapped())

    def test_single_document_split_close_cancel_and_last_file(self):
        e = self.editor
        original = e.active_document
        e.buffer.insert('1.0', 'unsaved original')
        e.split_editor.enable(); self.root.update()
        self.assertEqual(len(e.documents), 2)
        self.assertIsNot(e.active_document, original)
        with patch.object(core.messagebox, 'askyesnocancel', return_value=None):
            self.assertFalse(e.close_document(original))
        self.assertTrue(e.split_editor.enabled)
        self.assertTrue(original['widget'].winfo_ismapped())
        self.assertTrue(e.close_document(original)); self.root.update()
        self.assertFalse(e.split_editor.enabled)
        self.assertEqual(len(e.documents), 1)
        self.assertTrue(e.active_document['widget'].winfo_ismapped())

    def test_independent_scroll_settings_and_tab_replacement(self):
        e = self.editor
        e.set_document(None, '\n'.join(f'left {line}' for line in range(300)))
        left = e.active_document
        e.new(); e.set_document(None, '\n'.join(f'right {line}' for line in range(300)))
        right = e.active_document
        e.select_document(left); e.split_editor.enable(); self.root.update()
        left['widget'].yview_moveto(.5); self.root.update()
        self.assertGreater(left['widget'].yview()[0], .4)
        self.assertLess(right['widget'].yview()[0], .1)
        e.line_numbers.set(False); e.word_wrap.set(True); e.apply_settings(); self.root.update()
        for frame, title, gutter, vertical, horizontal in e.split_editor.panes:
            self.assertFalse(gutter.winfo_ismapped())
        self.assertEqual(left['widget'].cget('wrap'), 'word')
        self.assertEqual(right['widget'].cget('wrap'), 'word')
        e.new(); self.root.update()
        self.assertTrue(left['widget'].winfo_ismapped())
        self.assertFalse(right['widget'].winfo_ismapped())
        self.assertTrue(e.active_document['widget'].winfo_ismapped())
        e.line_numbers.set(True); e.apply_settings(); self.root.update()
        for frame, title, gutter, vertical, horizontal in e.split_editor.panes:
            self.assertTrue(gutter.winfo_ismapped())
            self.assertTrue(gutter.find_all())
