"""Tab appearance and read-only publisher information."""
import json
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import pocket_editor as core
import test_command_palette as fixtures


class TabsAboutTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown

    def test_notebook_and_document_tab_colors(self):
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup('TNotebook.Tab', 'foreground', ('selected',)), '#ffffff')
        self.assertEqual(style.lookup('TNotebook.Tab', 'background', ('selected',)), '#1e1e1e')
        self.assertEqual(style.lookup('TNotebook.Tab', 'foreground', ('disabled',)), '#777777')
        e = self.editor; first = e.active_document
        e.new(); second = e.active_document
        self.assertEqual(first['label'].cget('foreground'), '#bdbdbd')
        self.assertEqual(second['label'].cget('foreground'), '#ffffff')
        e.buffer.insert('1.0', 'modified'); self.root.update()
        self.assertEqual(second['label'].cget('foreground'), '#e5c07b')
        self.assertEqual(second['tab'].cget('background'), second['close'].cget('background'))

    def test_about_is_read_only_and_uses_published_author_information(self):
        path = core.SETTINGS_PATH.with_name('pocket_editor_about.json')
        legacy = {'name':'Local override', 'email':'local@example.invalid'}
        path.write_text(json.dumps(legacy))
        about = self.editor.about; about.show(); self.root.update()
        self.assertEqual(about.details['name'], 'Somi Douth')
        self.assertEqual(about.details['email'], 'somidouth@gmail.com')
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        buttons = [widget.cget('text') for widget in descendants(about.window) if isinstance(widget, ttk.Button)]
        self.assertNotIn('Edit details…', buttons)
        self.assertFalse(hasattr(about, 'edit'))
        self.assertFalse(hasattr(about, 'save'))
        self.assertTrue(all(str(widget.cget('state')) == 'readonly' for widget in descendants(about.info) if isinstance(widget, ttk.Entry)))
        about.copy()
        self.assertIn('Somi Douth', self.root.clipboard_get())
        self.assertIn('somidouth@gmail.com', self.root.clipboard_get())
        about.window.destroy(); about.show(); self.root.update()
        self.assertEqual(about.details['name'], 'Somi Douth')
        self.assertEqual(json.loads(path.read_text()), legacy)
