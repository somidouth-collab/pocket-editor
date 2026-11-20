"""Bounded plugin menu, scrolling and explicit dispatch."""
import unittest
import pocket_editor as core
import test_command_palette as fixtures

class ExtensionsMenuTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown

    def test_bounded_scrollable_groups_and_exact_dispatch(self):
        path = core.PLUGIN_DIR / 'many_commands.py'
        path.write_text('def register(api):\n' + ''.join(
            f' api.add_command("Action {index}", lambda: api.replace_text("ran {index}"))\n' for index in range(80)))
        self.editor.load_plugins();self.root.update()
        sidebar=self.editor.plugin_sidebar
        sidebar.show_commands();self.root.update()
        window=sidebar.command_window
        original=(window.winfo_width(),window.winfo_height())
        next(b for b in sidebar.command_buttons if b.cget('text')=='Many Commands  ›').invoke()
        self.root.update()
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'')
        self.assertEqual(original,(window.winfo_width(),window.winfo_height()))
        self.assertLessEqual(original[1],520)
        self.assertLess(sidebar.command_canvas.yview()[1],1)
        sidebar.command_canvas.event_generate('<Button-5>');self.root.update()
        self.assertGreater(sidebar.command_canvas.yview()[0],0)
        sidebar.command_buttons[-1].invoke();self.root.update()
        self.assertEqual(self.editor.buffer.get('1.0','end-1c'),'ran 79')
        self.assertFalse(window.winfo_exists())
        self.assertFalse(hasattr(self.editor,'plugin_strip'))
        self.editor.load_plugins();self.root.update()
        self.assertEqual(len(self.editor.plugin_commands),80)
        sidebar.show_commands();self.root.update()
        sidebar.show_commands('many_commands');self.root.update()
        self.assertEqual(sidebar.command_buttons[-1].cget('text'),'Action 79')
        sidebar.command_window.event_generate('<Escape>');self.root.update()
        self.assertFalse(sidebar.command_window.winfo_exists())
