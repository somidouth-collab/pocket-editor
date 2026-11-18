"""Portable defaults, ANSI parsing, and actual process-output colors in Tk."""
import json
import sys
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from tkinter import ttk

import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins import project_terminal as terminal
from pocket_editor_plugins._terminal_colors import AnsiDecoder, DEFAULTS, PRESETS, classify_line, color_value, validate_settings


def children(parent):
    for child in parent.winfo_children():
        yield child
        yield from children(child)


class AnsiTests(unittest.TestCase):
    def test_category_classification_is_explicit_and_conservative(self):
        for line, expected in [('USER: typed input', 'command'), ('SYSTEM: starting', 'status'),
                               ('WARNING: deprecated', 'warning'), ('src/main.c:2: error: bad', 'error'),
                               ('SUCCESS: completed', 'success'), ('Build succeeded', 'success'),
                               ('[2026-01-01] ERROR: failed', 'error'), ('ordinary output', None),
                               ('0 errors, 1 warning', None)]:
            with self.subTest(line=line): self.assertEqual(classify_line(line), expected)

    def test_split_sequences_extended_colors_reset_and_controls(self):
        decoder = AnsiDecoder()
        self.assertEqual(decoder.feed('\x1b[3'), [])
        runs = decoder.feed('1mRED\x1b[0m plain\r')
        self.assertEqual(runs[0], ('RED', (1, None, False, False, False)))
        self.assertEqual(runs[1][0], ' plain')
        self.assertEqual(decoder.feed('\nnext')[0][0], '\nnext')
        runs = decoder.feed('\x1b[1;4;38;2;12;34;56mRGB\x1b[0m\x1b]0;hidden title\x07visible')
        self.assertEqual(runs[0], ('RGB', ('#0c2238', None, True, True, False)))
        self.assertEqual(runs[-1][0], 'visible')
        settings = validate_settings({})
        self.assertEqual(color_value(196, settings, 'foreground'), '#ff0000')
        self.assertEqual(color_value(255, settings, 'foreground'), '#eeeeee')
        self.assertEqual(settings['encoding'], 'utf-8')
        self.assertEqual(settings['font_family'], '')
        with self.assertRaises(ValueError): validate_settings({'foreground': 'bad'})
        with self.assertRaises(ValueError): validate_settings({'encoding': 'hex_codec'})


class TerminalGuiTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.settings_patch = patch.object(terminal, 'SETTINGS_PATH', core.PLUGIN_DIR / 'terminal.json')
        self.settings_patch.start()
        self.api = core.PluginAPI(self.editor, 'project_terminal')
        terminal.register(self.api)
        self.editor.plugin_apis['project_terminal'] = self.api
        dict(self.api._commands)['Project Terminal']()
        self.root.update()
        self.window = next(widget for widget in self.root.winfo_children() if isinstance(widget, tk.Toplevel))

    def tearDown(self):
        try: fixtures.PaletteTests.tearDown(self)
        finally: self.settings_patch.stop()

    def button(self, label, parent=None):
        return next(widget for widget in children(parent or self.window) if isinstance(widget, ttk.Button) and widget.cget('text') == label)

    def test_semantic_process_output_and_ansi_precedence(self):
        script = core.PLUGIN_DIR / 'categories.py'
        script.write_text('import sys\nsys.stdout.write("USER: input\\nSYSTEM: starting\\nWARNING: old option\\nERROR: bad argument\\nSUCCESS: done\\n\\x1b[34mERROR: explicitly blue\\x1b[0m\\n")\n')
        command = next(widget for widget in children(self.window) if isinstance(widget, ttk.Entry) and widget.grid_info().get('row') == 1)
        command.insert(0, f'"{sys.executable}" "{script}"')
        self.button('Run').invoke()
        output = next(widget for widget in children(self.window) if isinstance(widget, tk.Text))
        deadline = time.monotonic() + 5
        while '[Exit code 0]' not in output.get('1.0', 'end') and time.monotonic() < deadline:
            self.root.update(); time.sleep(.02)
        for text, category in [('USER: input', 'command'), ('SYSTEM: starting', 'status'),
                               ('WARNING: old option', 'warning'), ('ERROR: bad argument', 'error'), ('SUCCESS: done', 'success')]:
            index = output.search(text, '1.0')
            self.assertTrue(index, text)
            self.assertIn('terminal_auto_' + category, output.tag_names(index))
        index = output.search('ERROR: explicitly blue', '1.0')
        names = output.tag_names(index)
        self.assertIn('terminal_auto_error', names)
        ansi = next(name for name in names if name.startswith('terminal_ansi_'))
        self.assertGreater(names.index(ansi), names.index('terminal_auto_error'))
        self.assertEqual(output.tag_cget(ansi, 'foreground'), DEFAULTS['palette'][4])
        self.button('Settings…').invoke(); self.root.update()
        dialog = next(widget for widget in children(self.window) if isinstance(widget, tk.Toplevel))
        toggle = next(widget for widget in children(dialog) if isinstance(widget, ttk.Checkbutton) and widget.cget('text') == 'Automatically color recognized plain-text categories')
        toggle.invoke(); self.button('Save & apply', dialog).invoke()
        self.assertFalse(self.api.call_service('project_terminal.get_settings')['categorize'])
        index = output.search('WARNING: old option', '1.0')
        self.assertNotIn('terminal_auto_warning', output.tag_names(index))

    def test_gnome_preset_preview_apply_and_restore(self):
        self.button('Settings…').invoke(); self.root.update()
        dialog = next(widget for widget in children(self.window) if isinstance(widget, tk.Toplevel))
        preset = next(widget for widget in children(dialog) if isinstance(widget, ttk.Combobox))
        self.assertEqual(preset.get(), 'Modern Graphite')
        preset.set('GNOME Tango'); preset.event_generate('<<ComboboxSelected>>'); self.root.update()
        preview = next(widget for widget in children(dialog) if isinstance(widget, tk.Text))
        self.assertEqual(preview.cget('background'), '#2e3436')
        self.assertEqual(preview.tag_cget('color_1', 'foreground'), '#cc0000')
        self.button('Save & apply', dialog).invoke(); self.root.update()
        settings = self.api.call_service('project_terminal.get_settings')
        self.assertEqual(settings['palette'], PRESETS['GNOME Tango']['palette'])
        self.assertEqual(settings['background'], '#2e3436')
        self.button('Restore defaults', dialog).invoke()
        self.button('Save & apply', dialog).invoke()
        self.assertEqual(self.api.call_service('project_terminal.get_settings'), validate_settings(DEFAULTS))

    def test_process_colors_settings_persistence_and_disable(self):
        script = core.PLUGIN_DIR / 'colored.py'
        script.write_text('import sys\nsys.stdout.write("\\x1b[31mRED\\x1b[0m plain\\n")\n')
        command = next(widget for widget in children(self.window) if isinstance(widget, ttk.Entry) and widget.grid_info().get('row') == 1)
        command.insert(0, f'"{sys.executable}" "{script}"')
        self.button('Run').invoke()
        output = next(widget for widget in children(self.window) if isinstance(widget, tk.Text))
        deadline = time.monotonic() + 5
        while '[Exit code 0]' not in output.get('1.0', 'end') and time.monotonic() < deadline:
            self.root.update(); time.sleep(.02)
        self.assertIn('RED plain', output.get('1.0', 'end'))
        self.assertNotIn('\x1b', output.get('1.0', 'end'))
        index = output.search('RED', '1.0')
        tag = next(name for name in output.tag_names(index) if name.startswith('terminal_ansi_'))
        self.assertEqual(output.tag_cget(tag, 'foreground'), '#e06c75')
        self.button('Settings…').invoke(); self.root.update()
        dialog = next(widget for widget in children(self.window) if isinstance(widget, tk.Toplevel))
        notebook = next(widget for widget in children(dialog) if isinstance(widget, ttk.Notebook))
        general = self.root.nametowidget(notebook.tabs()[0])
        foreground = next(widget for widget in general.winfo_children() if isinstance(widget, ttk.Entry) and widget.grid_info().get('row') == 1)
        foreground.delete(0, 'end'); foreground.insert(0, '#abcdef')
        ansi = next(widget for widget in general.winfo_children() if isinstance(widget, ttk.Checkbutton) and widget.cget('text') == 'Interpret ANSI colors')
        ansi.invoke()
        self.button('Save & apply', dialog).invoke(); self.root.update()
        settings = self.api.call_service('project_terminal.get_settings')
        self.assertEqual(settings['foreground'], '#abcdef'); self.assertFalse(settings['ansi'])
        self.assertEqual(output.cget('foreground'), '#abcdef')
        self.assertEqual(output.tag_cget(tag, 'foreground'), '')
        self.assertEqual(json.loads(terminal.SETTINGS_PATH.read_text())['_appearance']['foreground'], '#abcdef')
