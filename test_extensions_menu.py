"""Dropdown geometry, nested command dispatch, shared toolbar state, and reloads."""
import tkinter as tk
import unittest

import pocket_editor as core
import test_command_palette as fixtures


def find(menu, label):
    for index in range((menu.index('end') or 0) + 1):
        if menu.type(index) not in ('separator', 'tearoff') and menu.entrycget(index, 'label') == label:
            return index
    raise AssertionError(f'Missing menu entry: {label}')


class ExtensionsMenuTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown

    def test_grouping_geometry_commands_toolbar_and_reload(self):
        path = core.PLUGIN_DIR / 'many_commands.py'
        path.write_text('def register(api):\n' + ''.join(
            f' api.add_command("Action {index}", lambda: api.replace_text("ran"))\n' for index in range(35)))
        self.editor.load_plugins(); self.root.update()
        menu = self.editor.extensions_menu
        self.assertEqual(str(self.editor.extensions_button.cget('menu')), str(menu))
        self.assertNotEqual(str(menu), str(self.editor.plugins))
        index = find(menu, 'Many Commands')
        child = self.root.nametowidget(menu.entrycget(index, 'menu'))
        self.assertLessEqual(child.index('end'), 18)
        following = self.root.nametowidget(child.entrycget(find(child, 'More…'), 'menu'))
        self.assertGreaterEqual(find(following, 'Action 34'), 0)
        child.invoke(find(child, 'Action 0')); self.root.update()
        self.assertEqual(self.editor.buffer.get('1.0', 'end-1c'), 'ran')
        toolbar = self.root.nametowidget(menu.entrycget(find(menu, 'Add to toolbar'), 'menu'))
        toggles = self.root.nametowidget(toolbar.entrycget(find(toolbar, 'Many Commands'), 'menu'))
        toggles.invoke(find(toggles, 'Action 0')); self.root.update()
        self.assertIn('many_commands:Action 0', core.read_settings()['toolbar_commands'])
        self.assertTrue(self.editor.toolbar_variables['many_commands:Action 0'].get())
        count = len(menu.winfo_children())
        self.editor.load_plugins(); self.root.update()
        self.assertEqual(len(menu.winfo_children()), count)
        self.assertLess(menu.index('end'), 12)
        for width in (500, 1100):
            self.root.geometry(f'{width}x600'); self.root.update()
            menu.post(self.editor.extensions_button.winfo_rootx(),
                      self.editor.extensions_button.winfo_rooty() + self.editor.extensions_button.winfo_height())
            self.root.update()
            self.assertLess(menu.winfo_height(), self.root.winfo_screenheight() - 100)
            menu.unpost()
