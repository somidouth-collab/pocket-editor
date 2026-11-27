"""Real Tk checks for periodic external file detection, drafts, reload and save races."""
from pathlib import Path
import codecs
import hashlib
import os
import tempfile
import time
import unittest
from unittest.mock import patch
import pocket_editor as core
import test_command_palette as fixtures
from external_files import inspect_file


class ExternalFileTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.folder=core.SETTINGS_PATH.parent;self.path=self.folder/'example.txt';self.path.write_text('original\n')
        self.editor.open_path(self.path);self.watcher=self.editor.external_files
        self.until(lambda:self.watcher.current()['signature'] is not None)
        self.watcher.dismiss()
    tearDown=fixtures.PaletteTests.tearDown
    def until(self,predicate,timeout=5):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertTrue(predicate(),self.watcher.message.get())
    def changed(self):self.watcher.next_check=0
    def test_periodic_clean_reload_keeps_cursor_bom_and_is_undoable(self):
        text=self.editor.buffer;text.mark_set('insert','1.3')
        self.path.write_bytes(codecs.BOM_UTF8+b'external\r\nnext\r\n');self.changed()
        self.until(lambda:text.get('1.0','end-1c')=='external\nnext\n')
        self.assertEqual(text.index('insert'),'1.3');self.assertTrue(self.editor.bom)
        self.assertEqual(self.editor.newline,'\r\n');self.assertFalse(text.edit_modified())
        self.assertIn('Reloaded',self.watcher.message.get());self.assertTrue(self.editor.active_document['external_changed'])
        text.edit_undo();self.assertEqual(text.get('1.0','end-1c'),'original\n')
    def test_dirty_draft_kept_and_keep_mine_stops_repeating(self):
        self.editor.buffer.insert('end-1c','my draft');self.path.write_text('outside\n');self.changed()
        self.until(lambda:bool(self.watcher.current()['notice']))
        self.assertIn('my draft',self.editor.buffer.get('1.0','end-1c'))
        self.assertEqual(self.watcher.current()['notice'][0],'changed')
        self.watcher.keep();self.assertIsNone(self.watcher.current()['notice'])
        self.assertTrue(self.editor.buffer.edit_modified());self.assertEqual(self.path.read_text(),'outside\n')
        with patch.object(core.messagebox,'askyesno',side_effect=AssertionError('Already acknowledged')):
            self.assertTrue(self.editor.save())
        self.assertIn('my draft',self.path.read_text())
    def test_explicit_reload_cancel_and_accept_preserve_undo(self):
        text=self.editor.buffer;text.insert('end-1c','draft');self.path.write_text('outside\n');self.changed()
        self.until(lambda:bool(self.watcher.current()['notice']))
        with patch.object(core.messagebox,'askyesno',return_value=False):self.watcher.reload()
        self.assertIn('draft',text.get('1.0','end-1c'))
        with patch.object(core.messagebox,'askyesno',return_value=True):self.watcher.reload()
        self.until(lambda:text.get('1.0','end-1c')=='outside\n')
        text.edit_undo();self.assertIn('draft',text.get('1.0','end-1c'))
    def test_save_checks_disk_before_periodic_tick(self):
        self.editor.buffer.insert('end-1c','draft');self.path.write_text('newer disk\n')
        with patch.object(core.messagebox,'askyesno',return_value=False) as ask:
            self.assertFalse(self.editor.save());ask.assert_called_once()
        self.assertEqual(self.path.read_text(),'newer disk\n')
        with patch.object(core.messagebox,'askyesno',return_value=True):self.assertTrue(self.editor.save())
        self.assertIsNone(self.watcher.current()['notice'])
    def test_metadata_touch_and_own_save_do_not_report_external_change(self):
        verified=self.watcher.current()['verified']
        os.utime(self.path,None);self.changed()
        self.until(lambda:self.watcher.current()['verified']>verified)
        self.assertIsNone(self.watcher.current()['notice'])
        self.editor.buffer.insert('end-1c','saved');self.assertTrue(self.editor.save())
        self.until(lambda:self.watcher.current()['signature'] is not None)
        self.assertIsNone(self.watcher.current()['notice'])
    def test_deleted_and_invalid_utf8_preserve_open_copy(self):
        self.path.unlink();self.changed();self.until(lambda:self.watcher.current()['missing'])
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'original\n');self.assertTrue(self.editor.buffer.edit_modified())
        self.watcher.dismiss();self.changed()
        self.until(lambda:self.watcher.future is None)
        self.assertIsNone(self.watcher.current()['notice'])
        self.path.write_text('original\n');self.changed()
        self.until(lambda:not self.watcher.current()['missing'])
        self.assertIsNone(self.watcher.current()['notice'])
        self.path.write_bytes(b'\xff\xfeinvalid');self.changed()
        self.until(lambda:self.watcher.current()['notice'] is not None)
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'original\n')
        self.assertEqual(self.watcher.current()['notice'][0],'error')
    def test_inactive_document_reloads_without_focus_switch(self):
        first=self.editor.active_document;other=self.folder/'second.txt';other.write_text('second')
        self.editor.open_path(other);second=self.editor.active_document;self.editor.buffer.focus_force();self.root.update()
        self.path.write_text('first changed');self.changed()
        self.until(lambda:first['widget'].get('1.0','end-1c')=='first changed')
        self.assertIs(self.editor.active_document,second);self.assertIs(self.root.focus_get(),second['widget'])
        self.assertTrue(first['external_changed'])
    def test_atomic_replace_detected_and_own_save_invalidates_old_result(self):
        replacement=self.path.with_suffix('.tmp');replacement.write_text('atomic update')
        replacement.replace(self.path);self.changed()
        self.until(lambda:self.editor.buffer.get('1.0','end-1c')=='atomic update')
        state=self.watcher.current();old=state['serial'];key=str(self.editor.buffer.widget)
        self.editor.buffer.insert('end-1c',' saved');self.assertTrue(self.editor.save())
        self.watcher.apply((key,old,'content',None,'old',b'wrong stale result'))
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'atomic update saved')

    def test_saved_disk_baseline_survives_draft_snapshot(self):
        from editor_session import EditorSession
        self.editor.buffer.insert('end-1c','draft')
        snapshot=EditorSession(self.editor,self.folder/'session.json').snapshot()
        item=snapshot['documents'][snapshot['active']]
        self.assertEqual(item['disk_digest'],hashlib.sha256(b'original\n').hexdigest())
        self.assertIn('draft',item['text'])

    def test_closed_document_ignores_pending_result(self):
        state=self.watcher.current();key=str(self.editor.buffer.widget);serial=state['serial']
        document=self.editor.active_document;self.editor.close_document(document)
        self.watcher.apply((key,serial,'content',None,'x',b'outside'))
        self.assertIsNot(self.editor.active_document,document)
    def test_window_destroy_cancels_watcher_without_editor_quit(self):
        import tkinter as tk
        other=tk.Toplevel(self.root);editor=core.Editor(other,True)
        watcher=editor.external_files;job=watcher.job
        other.destroy();self.root.update()
        self.assertTrue(watcher.closed)
        self.assertNotIn(job,self.root.tk.splitlist(self.root.tk.call('after','info')))

    def test_open_menu_or_dialog_defers_clean_reload(self):
        import tkinter as tk
        dialog=tk.Toplevel(self.root);dialog.grab_set()
        state=self.watcher.current();key=str(self.editor.buffer.widget)
        result=(key,state['serial'],'content',None,hashlib.sha256(b'outside').hexdigest(),b'outside')
        self.watcher.apply(result)
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'original\n')
        dialog.grab_release();dialog.destroy();self.watcher.apply(result)
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'outside')

    def test_manual_reload_does_not_overwrite_new_edits(self):
        self.editor.buffer.insert('end-1c','draft')
        with patch.object(core.messagebox,'askyesno',return_value=True):self.watcher.reload()
        state=self.watcher.current();serial=state['serial'];key=str(self.editor.buffer.widget)
        self.editor.buffer.insert('end-1c','newer edit')
        self.watcher.apply((key,serial,'content',None,hashlib.sha256(b'outside').hexdigest(),b'outside'))
        self.assertIn('newer edit',self.editor.buffer.get('1.0','end-1c'))


class ProbeTests(unittest.TestCase):
    def test_large_unchanged_file_does_not_raise_false_change(self):
        from external_files import fingerprint
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'file.txt';data=b'unchanged data';path.write_bytes(data)
            signature=fingerprint(path);digest=hashlib.sha256(data).hexdigest()
            with patch('file_probe.AUTO_LIMIT',4):
                result=inspect_file(('doc',1,str(path),None,signature,digest,True,False))
                self.assertEqual(result[2],'same')
                path.write_text('changed data');signature=fingerprint(path)
                result=inspect_file(('doc',1,str(path),None,signature,digest,True,False))
                self.assertEqual(result[2],'large');self.assertNotEqual(result[4],digest)

    def test_stability_probe_does_not_read_first_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'file.txt';path.write_text('text')
            result=inspect_file(('doc',1,str(path),None,None,None,True,False))
            self.assertEqual(result[2],'settling')
            result=inspect_file(('doc',1,str(path),None,result[3],None,True,False))
            self.assertEqual(result[2],'content');self.assertEqual(result[-1],b'text')
