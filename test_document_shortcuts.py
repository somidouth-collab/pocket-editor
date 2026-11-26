"""Real document keyboard shortcuts, clipboard, history, and tab routing."""
import test_command_palette as fixtures


import unittest


class DocumentShortcutTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown
    # Reuse only the isolated editor fixture, not its tests.
    def key(self, key):
        self.editor.buffer.focus_force();self.root.update()
        self.editor.buffer.event_generate(f"<{self.editor.modifier}-{key}>")
        self.root.update()

    def test_clipboard_select_all_and_history(self):
        text=self.editor.buffer
        text.insert('1.0','hello world');text.edit_reset()
        self.key('a');self.assertEqual(text.get('sel.first','sel.last'),'hello world')
        self.key('c');self.assertEqual(self.root.clipboard_get(),'hello world')
        self.assertEqual(text.get('1.0','end-1c'),'hello world')
        self.key('x');self.assertEqual(text.get('1.0','end-1c'),'')
        self.key('z');self.assertEqual(text.get('1.0','end-1c'),'hello world')
        self.key('y');self.assertEqual(text.get('1.0','end-1c'),'')
        self.key('z');self.key('Shift-Z');self.assertEqual(text.get('1.0','end-1c'),'')
        self.key('v');self.assertEqual(text.get('1.0','end-1c'),'hello world')
        self.key('z');self.assertEqual(text.get('1.0','end-1c'),'')
        text.edit_reset()
        for key in ('z','y','Shift-Z','c','x'):self.key(key)
        self.assertEqual(text.get('1.0','end-1c'),'')

    def test_shortcuts_follow_selected_document(self):
        editor=self.editor;editor.buffer.insert('1.0','first');first=editor.active_document
        editor.new();editor.buffer.insert('1.0','second')
        self.key('a');self.key('c');self.assertEqual(self.root.clipboard_get(),'second')
        editor.select_document(first);self.key('a');self.key('v')
        self.assertEqual(first['widget'].get('1.0','end-1c'),'second')
        self.key('z');self.assertEqual(first['widget'].get('1.0','end-1c'),'first')

    def test_edit_menu_lists_shortcuts(self):
        editor=self.editor
        menu=next(editor.menu_bar.nametowidget(editor.menu_bar.entrycget(i,'menu'))
                  for i in range(editor.menu_bar.index('end')+1)
                  if editor.menu_bar.type(i)=='cascade' and editor.menu_bar.entrycget(i,'label')=='Edit')
        modifier='Cmd' if editor.modifier=='Command' else 'Ctrl'
        for label,key in (('Copy','C'),('Cut','X'),('Paste','V'),('Undo','Z'),('Redo','Y'),('Select All','A')):
            self.assertEqual(menu.entrycget(label,'accelerator'),f'{modifier}+{key}')

    def test_line_tab_and_zoom_shortcuts(self):
        import pocket_editor as core
        from pocket_editor_plugins import editor_commands
        api=core.PluginAPI(self.editor,'editor_commands')
        editor_commands.register(api)
        self.editor.buffer.insert('1.0','one\ntwo')
        self.editor.buffer.mark_set('insert','1.0')
        self.key('d');self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'one\none\ntwo')
        self.key('z');self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'one\ntwo')
        self.key('Shift-K');self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'two')
        self.key('z');self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'one\ntwo')
        first=self.editor.active_document;self.editor.new();second=self.editor.active_document
        self.key('Tab');self.assertIs(self.editor.active_document,first)
        self.key('Shift-Tab');self.assertIs(self.editor.active_document,second)
        size=self.editor.editor_font.cget('size');self.key('equal')
        self.assertEqual(self.editor.editor_font.cget('size'),size+1)
        self.key('minus');self.assertEqual(self.editor.editor_font.cget('size'),size)
