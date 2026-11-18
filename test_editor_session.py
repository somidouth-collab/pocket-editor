"""Session persistence, interrupted-session recovery, and clean discard tests."""
import json
import tkinter as tk
import unittest
from unittest.mock import patch

import pocket_editor as core
import test_command_palette as fixtures
from editor_session import EditorSession


class SessionTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        for item in (patch.object(core.messagebox, 'askyesno', return_value=True),
                     patch.object(core.messagebox, 'showwarning')):
            item.start()
            self.patches.append(item)
    tearDown = fixtures.PaletteTests.tearDown

    def session(self):
        return EditorSession(self.editor, core.SETTINGS_PATH.with_name('session.json'))

    def restart(self, session):
        if session.job:
            self.root.after_cancel(session.job)
        session.release()
        for job in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(job)
        self.root.destroy()
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.editor = core.Editor(self.root, False)
        self.root.update()
        restored = self.session()
        self.assertTrue(restored.start())
        self.root.update()
        return restored

    def test_clean_tabs_cursor_and_discard(self):
        first = core.PLUGIN_DIR / 'one.txt'; first.write_text('disk one\nsecond')
        second = core.PLUGIN_DIR / 'two.txt'; second.write_text('disk two')
        session = self.session(); self.assertTrue(session.start())
        self.editor.open_path(first)
        self.editor.buffer.mark_set('insert', '2.3')
        self.editor.open_path(second)
        self.editor.buffer.insert('end-1c', ' discarded')
        self.editor.select_document(self.editor.documents[0])
        self.editor.new(); self.editor.buffer.insert('1.0', 'discarded untitled')
        self.editor.select_document(self.editor.documents[0])
        self.editor.quit()
        data = json.loads(session.path.read_text())
        self.assertTrue(data['clean'])
        self.assertEqual(len(data['documents']), 2)
        self.assertFalse(any('text' in item for item in data['documents']))
        self.assertEqual(second.read_text(), 'disk two')
        # Quit already destroyed this root; create a fresh editor for restore.
        self.root = tk.Tk(); self.editor = core.Editor(self.root, False); self.root.update()
        restored = self.session(); self.assertTrue(restored.start()); self.root.update()
        self.assertEqual(len(self.editor.documents), 2)
        self.assertEqual(self.editor.path, first)
        self.assertEqual(self.editor.buffer.index('insert'), '2.3')

    def test_recover_unsaved_tabs_formatting_newlines_and_active_tab(self):
        path = core.PLUGIN_DIR / 'file.txt'; path.write_text('original')
        session = self.session(); self.assertTrue(session.start())
        self.editor.open_path(path)
        self.editor.buffer.insert('end-1c', ' edited')
        self.editor.new()
        self.editor.buffer.insert('1.0', 'draft\nnext')
        self.editor.newline = '\r\n'; self.editor.bom = True
        self.editor.buffer.tag_configure('rich_example', foreground='red')
        self.editor.buffer.tag_add('rich_example', '1.0', '1.5')
        self.editor.rich_dirty = True
        self.editor.buffer.mark_set('insert', '2.2')
        self.assertTrue(session.write())
        with patch.object(core.messagebox, 'askyesno', return_value=True):
            self.restart(session)
        self.assertEqual(len(self.editor.documents), 2)
        self.assertIsNone(self.editor.path)
        self.assertEqual(self.editor.buffer.get('1.0', 'end-1c'), 'draft\nnext')
        self.assertEqual(self.editor.buffer.index('insert'), '2.2')
        self.assertEqual(self.editor.newline, '\r\n'); self.assertTrue(self.editor.bom)
        self.assertTrue(self.editor.rich_dirty)
        self.assertTrue(self.editor.buffer.tag_ranges('rich_recovered_0'))
        self.assertEqual(path.read_text(), 'original')
        self.editor.select_document(self.editor.documents[0])
        self.assertEqual(self.editor.buffer.get('1.0', 'end-1c'), 'original edited')
        self.assertTrue(self.editor.buffer.edit_modified())

    def test_decline_recovery_lock_and_invalid_backup(self):
        session = self.session(); self.assertTrue(session.start())
        other = self.session(); self.assertFalse(other.start())
        self.editor.buffer.insert('1.0', 'do not recover')
        session.write()
        with patch.object(core.messagebox, 'askyesno', return_value=False):
            restored = self.restart(session)
        self.assertEqual(self.editor.buffer.get('1.0', 'end-1c'), '')
        self.root.after_cancel(restored.job); restored.release(); self.editor.session = None
        restored.path.write_text('{broken')
        with patch.object(core.messagebox, 'showwarning'):
            replacement = self.session(); self.assertTrue(replacement.start())
        self.assertEqual(replacement.path.with_suffix('.invalid.json').read_text(), '{broken')

    def test_canceled_quit_retains_recovery(self):
        session = self.session(); self.assertTrue(session.start())
        self.editor.buffer.insert('1.0', 'retain')
        session.write()
        with patch.object(core.messagebox, 'askyesnocancel', return_value=None):
            self.editor.quit()
        self.assertFalse(self.editor.closing)
        self.assertFalse(json.loads(session.path.read_text())['clean'])
        self.assertIsNotNone(session.lock)
