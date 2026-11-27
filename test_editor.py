"""Real Tk regression suite; run on a desktop: python3 -B -m unittest -v test_editor."""
import codecs
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import pocket_editor as core
from pocket_editor_plugins import search_replace


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pocket-editor-test-')
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.settings = patch.object(core, 'SETTINGS_PATH', self.folder / 'settings.json')
        self.settings.start()
        self.dialogs = []
        for name, result in [('showerror', None), ('showinfo', None),
                             ('askyesno', True), ('askyesnocancel', False)]:
            mock = patch.object(core.messagebox, name, return_value=result)
            self.dialogs.append(mock)
            setattr(self, name, mock.start())
        self.editor = core.Editor(self.root, False)
        deadline=time.monotonic()+10
        while not self.editor.plugins_ready and time.monotonic()<deadline:self.pump(.01)
        self.assertTrue(self.editor.plugins_ready)
        self.pump()

    def tearDown(self):
        try:
            if not self.editor.closing:
                self.editor.buffer.edit_modified(False)
                self.editor.rich_dirty = False
                self.editor.quit()
            self.assertEqual(self.errors, [])
        finally:
            for mock in self.dialogs:
                mock.stop()
            self.settings.stop()
            self.temp.cleanup()

    def pump(self, seconds=0.03):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)

    def until(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump(.03)
        self.assertTrue(predicate(), "Timed out waiting for GUI result")

    def command(self, label):
        self.editor.plugins.invoke(label)
        self.pump()

    def button(self, label):
        choices = [w for w in widgets(self.root) if isinstance(w, ttk.Button) and w.cget('text') == label]
        self.assertTrue(choices, label)
        choices[-1].invoke()
        self.pump()

    def window(self, title):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=next((w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel) and w.title()==title),None)
            if result is not None:return result
            self.pump(.01)
        self.fail('Window did not open: '+title+'; '+str(self.editor.plugin_errors))

    def entry(self, window, row):
        return next(w for w in widgets(window) if isinstance(w, ttk.Entry) and str(w.grid_info().get('row')) == str(row))

    def set_entry(self, widget, value):
        widget.delete(0, 'end')
        widget.insert(0, value)

    def test_loading_layout_services_and_failure_cleanup(self):
        e = self.editor
        expected_plugins = {path.stem for path in core.PLUGIN_DIR.glob("*.py")
                            if not path.name.startswith("_")}
        self.assertEqual(set(e.plugin_apis), expected_plugins)
        self.assertEqual(list(e.plugin_apis), sorted(e.plugin_apis))
        before = e.plugins.index('end')
        e.load_plugins(); self.pump()
        self.assertEqual(before, e.plugins.index('end'))
        self.assertIn('code_theme.refresh', e.plugin_apis['code_theme'].list_services())
        self.command('Project Explorer')
        self.assertEqual(e.status.winfo_width(), self.root.winfo_width())
        self.root.geometry('500x600'); self.pump()
        self.assertFalse(hasattr(e, "plugin_strip"))
        p = self.folder / 'bad.py'
        p.write_text('def register(api):\n api.on_cleanup(lambda: setattr(api._editor, "cleaned", True))\n api.provide_service("bad", lambda: None)\n raise ValueError("intentional")\n')
        with patch.object(core, 'PLUGIN_DIR', self.folder):
            e.load_plugins()
        self.assertTrue(e.cleaned)
        self.assertNotIn('bad', e.plugin_apis)
        with self.assertRaises(ValueError):
            e.plugin_apis['code_theme'].emit('', source='bad')
        seen = []
        api = e.plugin_apis['word_count']
        remove = api.on('test', lambda **kw: seen.append(kw))
        e.dispatch_plugin_event('test', 'test', {})
        remove(); e.dispatch_plugin_event('test', 'test', {})
        self.assertEqual(len(seen), 1)
        api.on('broken', lambda **kw: 1 / 0)
        api.on('broken', lambda **kw: seen.append(kw))
        e.dispatch_plugin_event('broken', 'test', {})
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(self.errors), 1); self.errors.clear()

    def test_plugins_use_menu_and_vertical_tabs_only(self):
        e = self.editor
        self.assertFalse(hasattr(e, 'plugin_strip'))
        self.assertFalse(hasattr(e, 'toolbar_menu'))
        core.SETTINGS_PATH.write_text('{"toolbar_commands": ["code_theme:Code Theme"]}')
        self.assertNotIn('toolbar_commands', core.read_settings())
        e.save_settings()
        self.assertNotIn('toolbar_commands', core.SETTINGS_PATH.read_text())
        e.command_palette.show();self.pump()
        self.assertTrue(any('Code Theme' in row[0] for row in e.command_palette.commands))

    def test_tabs_preserve_undo_cursor_formatting_and_plugins(self):
        e = self.editor
        first = self.folder / 'first.py'; first.write_text('if True: pass')
        second = self.folder / 'second.txt'; second.write_text('second')
        e.open_path(first); self.pump()
        first_tab = e.active_document
        e.buffer.edit_separator()
        e.buffer.insert('end-1c', '\nchanged')
        e.buffer.edit_separator()
        e.buffer.mark_set('insert', '1.3')
        e.buffer.tag_configure('rich_test', foreground='red')
        e.buffer.tag_add('rich_test', '1.0', '1.2')
        e.rich_dirty = True
        e.open_path(second); self.pump()
        self.assertEqual(len(e.documents), 2)
        self.assertEqual(e.buffer.get('1.0', 'end-1c'), 'second')
        self.assertFalse(e.rich_dirty)
        e.select_document(first_tab); self.pump(.3)
        self.assertEqual(e.buffer.index('insert'), '1.3')
        self.assertTrue(e.rich_dirty)
        self.assertTrue(e.buffer.tag_ranges('rich_test'))
        self.assertTrue(e.buffer.tag_ranges('syntax_keyword'))
        self.assertIn('●', first_tab['label'].cget('text'))
        e.buffer.edit_undo()
        self.assertEqual(e.buffer.get('1.0', 'end-1c'), 'if True: pass')
        e.open_path(first)
        self.assertEqual(len(e.documents), 2)
        self.assertIs(e.active_document, first_tab)

    def test_tab_close_save_cancel_failure_and_last_tab(self):
        e = self.editor
        path = self.folder / 'close.txt'; path.write_text('original')
        e.open_path(path)
        tab = e.active_document
        e.buffer.insert('end-1c', ' edited')
        e.new(); current = e.active_document
        with patch.object(core.messagebox, 'askyesnocancel', return_value=None):
            self.assertFalse(e.close_document(tab))
        self.assertIs(e.active_document, current)
        self.assertIn(tab, e.documents)
        with patch.object(core.messagebox, 'askyesnocancel', return_value=True), patch.object(core.os, 'replace', side_effect=OSError('failed')):
            self.assertFalse(e.close_document(tab))
        self.assertEqual(path.read_text(), 'original')
        with patch.object(core.messagebox, 'askyesnocancel', return_value=True):
            self.assertTrue(e.close_document(tab))
        self.assertEqual(path.read_text(), 'original edited')
        self.assertTrue(e.close_document())
        self.assertEqual(len(e.documents), 1)
        self.assertIsNone(e.path)

    def test_save_all_duplicate_save_as_and_quit_cancel(self):
        e = self.editor
        first = self.folder / 'one.txt'; first.write_text('one')
        second = self.folder / 'two.txt'; second.write_text('two')
        e.open_path(first); e.buffer.insert('end-1c', '!')
        e.open_path(second); e.buffer.insert('end-1c', '!')
        self.assertFalse(e.write(first))
        self.assertEqual(first.read_text(), 'one')
        self.assertTrue(e.save_all())
        self.assertEqual(first.read_text(), 'one!')
        self.assertEqual(second.read_text(), 'two!')
        e.select_document(e.documents[0]); e.buffer.insert('end-1c', '?')
        e.select_document(e.documents[1])
        with patch.object(core.messagebox, 'askyesnocancel', return_value=None):
            e.quit()
        self.assertFalse(e.closing)
        self.assertEqual(len(e.documents), 2)
        self.assertEqual(e.path, second)
        e.new(); e.buffer.insert('end-1c', 'untitled')
        with patch.object(core.messagebox, 'askyesnocancel', return_value=True), patch.object(core.filedialog, 'asksaveasfilename', return_value=''):
            self.assertFalse(e.close_document())
        self.assertEqual(e.buffer.get('1.0', 'end-1c'), 'untitled')

    def test_many_tabs_wrap_and_remain_accessible(self):
        e = self.editor
        for index in range(9):
            path = self.folder / f'file-{index}-with-a-very-long-document-name.txt'; path.write_text(str(index))
            e.open_path(path)
        self.root.geometry('500x600'); self.pump()
        self.assertEqual(len(e.documents), 9)
        self.assertGreater(max(document['tab'].grid_info()['row'] for document in e.documents), 0)
        for document in e.documents:
            self.assertTrue(document['close'].winfo_ismapped())
            self.assertLessEqual(document['tab'].winfo_x() + document['tab'].winfo_width(), e.tabs.winfo_width())

    def test_atomic_saves_bom_newlines_failure_and_links(self):
        e = self.editor
        path = self.folder / 'document.txt'
        original = codecs.BOM_UTF8 + b'one\r\ntwo\r\n'
        path.write_bytes(original)
        e.open_path(path); self.pump()
        self.assertEqual(e.plugin_apis['word_count'].get_text(), 'one\ntwo\n')
        e.buffer.insert('end-1c', 'three\n')
        with patch.object(core.os, 'replace', side_effect=OSError('injected failure')):
            self.assertFalse(e.save())
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(e.buffer.edit_modified())
        self.assertEqual(list(self.folder.glob('.document.txt.*')), [])
        self.assertTrue(e.save())
        self.assertEqual(path.read_bytes(), original + b'three\r\n')
        if os.name != 'nt':
            link = self.folder / 'link.txt'; link.symlink_to(path)
            e.path = link; e.buffer.insert('end-1c', 'linked\n'); self.assertTrue(e.save())
            self.assertTrue(link.is_symlink()); self.assertTrue(path.read_bytes().endswith(b'linked\r\n'))
        e.buffer.insert('end-1c', 'retain')
        e.open_path(self.folder / 'missing')
        self.assertTrue(e.plugin_apis['word_count'].get_text().endswith('retain'))
        with patch.object(core.messagebox, 'askyesnocancel', return_value=True), patch.object(e, 'save', return_value=False):
            e.new()
        self.assertEqual(e.plugin_apis['word_count'].get_text(), '')
        e.select_document(e.documents[0])
        self.assertTrue(e.plugin_apis['word_count'].get_text().endswith('retain'))

    def test_gutter_alignment_wrap_scroll_resize_and_safe_mode(self):
        e = self.editor
        e.set_document(None, ("long document line " * 15 + "\n") * 60)
        large = core.font.Font(root=self.root, family=e.editor_font.cget("family"), size=28)
        e.buffer.tag_configure("mixed_font", font=large)
        e.buffer.tag_add("mixed_font", "2.0", "2.9")
        for wrapped, size, fraction in ((False, "820x560", 0), (True, "600x600", 0),
                                        (True, "700x600", .4), (False, "900x560", .7)):
            e.word_wrap.set(wrapped); e.apply_settings()
            self.root.geometry(size); self.pump()
            e.buffer.yview_moveto(fraction); self.pump()
            e.draw_gutter()
            self.assertTrue(e.gutter.find_all())
            self.assertGreater(e.status.winfo_height(), 1)
            for item in e.gutter.find_all():
                line = e.gutter.itemcget(item, "text")
                info = e.buffer.dlineinfo(line + ".0")
                self.assertIsNotNone(info)
                expected = info[1] + info[4] - e.gutter_font.metrics("ascent")
                self.assertAlmostEqual(e.gutter.coords(item)[1], expected)
        other = tk.Toplevel(self.root)
        safe = core.Editor(other, True)
        self.pump()
        self.assertFalse(safe.plugin_apis)
        self.assertEqual(safe.plugins.entrycget(0, "state"), "disabled")
        other.destroy()

    def test_settings_validation_and_font_isolation(self):
        core.SETTINGS_PATH.write_text('{"font_size": true, "tab_width": 0, "line_numbers": "false"}')
        self.assertEqual(core.read_settings(), core.DEFAULT_SETTINGS)
        e = self.editor
        gutter_font = e.gutter_font.actual()
        default_font = core.font.nametofont('TkDefaultFont').actual()
        e.editor_font.configure(size=25); e.tab_width = 8; e.save_settings(); self.pump()
        self.assertEqual(e.gutter_font.actual(), gutter_font)
        self.assertEqual(core.font.nametofont('TkDefaultFont').actual(), default_font)
        self.assertEqual(core.read_settings()['font_size'], 25)
        saved = core.SETTINGS_PATH.read_bytes()
        with patch.object(core.os, 'replace', side_effect=OSError('settings failure')):
            e.save_settings()
        self.assertEqual(core.SETTINGS_PATH.read_bytes(), saved)

    def test_changes_unicode_highlight_save_as_undo_redo(self):
        e = self.editor
        api = e.plugin_apis['word_count']
        e.set_document(self.folder / 'a.py', '# 😀\nif True: pass\n')
        self.pump(.3)
        self.assertEqual(e.buffer.get('syntax_keyword.first', 'syntax_keyword.first +2c'), 'if')
        e.buffer.insert('end-1c', 'return\n'); self.pump(.3)
        e.buffer.insert('end-1c', 'else\n'); self.pump(.3)
        self.assertIn('4.0', list(map(str, e.buffer.tag_ranges('syntax_keyword'))))
        e.set_document(None, 'alpha')
        api.replace_text('beta'); self.pump()
        e.buffer.event_generate('<<Undo>>'); self.pump(); self.assertEqual(api.get_text(), 'alpha')
        e.buffer.event_generate('<<Redo>>'); self.pump(); self.assertEqual(api.get_text(), 'beta')
        e.set_document(None, 'if True: pass')
        with patch.object(core.filedialog, 'asksaveasfilename', return_value=str(self.folder/'new.py')):
            e.save_as()
        self.pump(.3)
        self.assertTrue(e.buffer.tag_ranges('syntax_keyword'))
        e.write(self.folder / 'plain.txt'); self.pump(.3)
        self.assertFalse(e.buffer.tag_ranges('syntax_keyword'))

    def test_rich_text_roundtrip_priority_dirty_and_document_switch(self):
        e = self.editor
        e.set_document(self.folder / 'a.py', 'if True: pass\nsecond line')
        self.command('Rich Text')
        e.buffer.tag_add('sel', '1.0', '1.2')
        self.button('Bold')
        tags = [tag for tag in e.buffer.tag_names('1.0') if tag.startswith('rich_')]
        self.assertTrue(tags); self.assertTrue(e.rich_dirty)
        self.pump(.3)
        names = list(e.buffer.tag_names())
        self.assertGreater(names.index(tags[-1]), names.index('syntax_keyword'))
        rich = self.folder / 'copy.pocket'
        with patch.object(core.filedialog, 'asksaveasfilename', return_value=str(rich)):
            self.button('Save Formatted Copy…')
        self.assertFalse(e.rich_dirty)
        e.set_document(None, 'different')
        self.assertFalse(any(e.buffer.tag_ranges(t) for t in tags))
        with patch.object(core.filedialog, 'askopenfilename', return_value=str(rich)):
            self.button('Open Formatted…')
        self.assertEqual(e.plugin_apis['word_count'].get_text(), 'if True: pass\nsecond line')
        self.assertTrue(any(e.buffer.tag_ranges(t) for t in tags))
        e.write(self.folder/'plain.txt')
        self.assertTrue(e.rich_dirty)
        with patch.object(core.messagebox, 'askyesno', return_value=False):
            e.new()
        self.assertIsNone(e.path)
        self.assertTrue(any(document['path'] == self.folder/'plain.txt' and document['rich_dirty'] for document in e.documents))
        corrupt = self.folder/'bad.pocket'; corrupt.write_text('{"format":"pocket-rich-1","text":"lost","styles":[{}]}')
        with patch.object(core.filedialog, 'askopenfilename', return_value=str(corrupt)):
            self.button('Open Formatted…')
        self.assertNotEqual(e.plugin_apis['word_count'].get_text(), 'lost')

    def test_search_ui_unicode_zero_length_replacement_undo(self):
        e = self.editor
        e.set_document(None, '😀 café café')
        self.command('Search & Replace — Separate Window')
        window = self.window('Search and Replace')
        self.set_entry(self.entry(window, 1), 'café')
        self.set_entry(self.entry(window, 3), 'tea')
        self.button('Next')
        self.assertEqual(e.buffer.get('sel.first', 'sel.last'), 'café')
        self.button('Replace')
        self.assertEqual(e.plugin_apis['word_count'].get_text(), '😀 tea café')
        e.buffer.event_generate('<<Undo>>'); self.pump()
        self.assertEqual(e.plugin_apis['word_count'].get_text(), '😀 café café')
        for w in widgets(window):
            if isinstance(w, ttk.Checkbutton) and w.cget('text') == 'Regular expression': w.invoke()
        self.set_entry(self.entry(window, 1), '(?=café)')
        self.button('Next'); first=e.buffer.index('insert')
        self.button('Next'); self.assertNotEqual(first, e.buffer.index('insert'))
        self.set_entry(self.entry(window, 1), '(café)')
        self.set_entry(self.entry(window, 3), r'\2')
        before=e.plugin_apis['word_count'].get_text(); self.button('Replace All')
        self.assertEqual(e.plugin_apis['word_count'].get_text(), before)
        self.set_entry(self.entry(window, 3), r'<\1>'); self.button('Replace All')
        self.assertEqual(e.plugin_apis['word_count'].get_text(), '😀 <café> <café>')
        start=time.monotonic()
        with self.assertRaises(ValueError):
            search_replace.bounded_search('a'*10000+'!', '(a+)+$', True, True)
        self.assertLess(time.monotonic()-start, 2)

    def test_text_formatting_ui_undo(self):
        e=self.editor; e.set_document(None, 'alpha\nbeta\n')
        self.command('Text Formatting'); self.button('Apply Formatting')
        self.assertEqual(e.plugin_apis['word_count'].get_text(), 'ALPHA\nBETA\n')
        e.buffer.event_generate('<<Undo>>'); self.pump()
        self.assertEqual(e.plugin_apis['word_count'].get_text(), 'alpha\nbeta\n')

    def test_explorer_rename_parent_collision_delete(self):
        e=self.editor
        folder=self.folder/'sub'; folder.mkdir(); path=folder/'open.py'; path.write_text('preserve')
        e.open_path(path)
        with patch.object(core.filedialog, 'askdirectory', return_value=str(self.folder)):
            self.command('Open Project')
        tree=next(w for w in widgets(self.root) if isinstance(w, ttk.Treeview))
        node=next(n for n in tree.get_children(tree.get_children()[0]) if tree.item(n,'text')=='sub/')
        tree.selection_set(node); tree.focus(node)
        menu=next(w for w in widgets(tree) if isinstance(w, tk.Menu))
        (self.folder/'collision').mkdir()
        with patch.object(core.simpledialog,'askstring',return_value='collision'): menu.invoke('Rename…')
        self.assertTrue(path.exists())
        with patch.object(core.simpledialog,'askstring',return_value='renamed'): menu.invoke('Rename…')
        self.assertEqual(e.path, self.folder/'renamed'/'open.py')
        e.buffer.insert('end-1c',' edited'); self.assertTrue(e.save())
        node=next(n for n in tree.get_children(tree.get_children()[0]) if tree.item(n,'text')=='renamed/')
        tree.selection_set(node); menu.invoke('Delete…'); self.pump()
        self.assertIsNone(e.path); self.assertTrue(e.buffer.edit_modified())
        self.assertEqual(e.plugin_apis['word_count'].get_text(), 'preserve edited')

    def test_terminal_output_fallback_stop_and_shutdown(self):
        e=self.editor;e.set_document(self.folder/'file.txt','')
        self.command('Project Terminal')
        self.until(lambda:'project_terminal' in e.plugin_sidebar.hosts and 'project_terminal' not in e.plugin_sidebar.pending)
        window=e.plugin_sidebar.hosts['project_terminal']._panel_widget
        api=e.plugin_apis['project_terminal']
        output=next(w for w in widgets(window) if isinstance(w,tk.Text) and getattr(w,'_terminal_role','')=='runner')
        def run(command):api.call_service('project_terminal.run_command',command,str(self.folder))
        run(f'"{sys.executable}" -c "print(\'hello 😀\')"');self.pump(.6)
        self.assertIn('hello 😀',output.get('1.0','end'))
        self.assertIn('[Exit code 0]',output.get('1.0','end'))
        run(f'"{sys.executable}" -c "import sys; sys.stdout.write(\'x\'*1200000)"');self.pump(1.5)
        self.until(lambda:"[Exit code 0]" in output.get('end-3l','end'))
        self.assertLessEqual(int(output.count('1.0','end-1c','chars')[0]),200000)
        marker=self.folder/'pid'
        command=f'"{sys.executable}" -c "import os,time; open(\'{marker}\',\'w\').write(str(os.getpid())); time.sleep(60)"'
        run(command);self.until(marker.exists);pid=int(marker.read_text())
        api.call_service('project_terminal.stop_command');self.pump(.4)
        if os.name=='posix':
            stat=Path(f'/proc/{pid}/stat')
            self.assertTrue(not stat.exists() or stat.read_text().split()[2]=='Z')
        marker.unlink();run(command);self.until(marker.exists);pid=int(marker.read_text())
        e.buffer.edit_modified(False);e.quit()
        if os.name=='posix':
            stat=Path(f'/proc/{pid}/stat')
            self.assertTrue(not stat.exists() or stat.read_text().split()[2]=='Z')

    @unittest.skipUnless(importlib.util.find_spec('black'), 'Black is optional')
    def test_black_service_and_undo(self):
        e=self.editor; e.set_document(self.folder/'sample.py','x=  [1,2,3]\n')
        e.plugin_apis['word_count'].call_service('python_tools.format')
        self.until(lambda:e.plugin_apis['word_count'].get_text()=='x = [1, 2, 3]\n')
        self.assertEqual(e.plugin_apis['word_count'].get_text(),'x = [1, 2, 3]\n')
        e.buffer.event_generate('<<Undo>>'); self.pump()
        self.assertEqual(e.plugin_apis['word_count'].get_text(),'x=  [1,2,3]\n')


if __name__=='__main__':
    unittest.main()
