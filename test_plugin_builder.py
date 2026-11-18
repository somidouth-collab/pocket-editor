"""Builder generation, loading, and real Tk integration tests."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from tkinter import ttk

import pocket_editor as core
from pocket_editor_plugins import plugin_builder as builder


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


class GeneratorTests(unittest.TestCase):
    def test_templates_compile_and_register(self):
        for template in builder.TEMPLATES:
            with self.subTest(template=template):
                source = builder.build_source('example_plugin', 'Say "Hello"', 'Quotes " and \\ paths\nNew line', template)
                scope = {}
                exec(compile(source, '<generated>', 'exec'), scope)
                editor = type('Editor', (), {'root': None})()
                api = core.PluginAPI(editor, 'example_plugin')
                scope['register'](api)
                self.assertEqual(api._commands[0][0], 'Say "Hello"')
                if template == 'Text transform':
                    self.assertEqual(scope['transform']('Hello\n'), 'HELLO\n')
                if template == 'Events and services':
                    self.assertIn('word_count', api._services)
                    self.assertEqual(api._subscriptions[0][0], 'document_saved')

    def test_invalid_fields_and_exclusive_creation(self):
        for plugin_id in ('../escape', '/tmp/nope', '_hidden', 'class', 'Bad Name', 'a.b', '', 'a' * 65):
            with self.subTest(plugin_id=plugin_id), self.assertRaises(ValueError):
                builder.build_source(plugin_id, 'Label', '', builder.TEMPLATES[0])
        for label in ('', 'bad\nlabel', 'x' * 101):
            with self.assertRaises(ValueError):
                builder.build_source('valid', label, '', builder.TEMPLATES[0])
        with tempfile.TemporaryDirectory() as folder:
            path = builder.create_plugin(folder, 'valid', 'Label', '', builder.TEMPLATES[0])
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                builder.create_plugin(folder, 'valid', 'Changed', '', builder.TEMPLATES[1])
            self.assertEqual(path.read_bytes(), before)


class BuilderGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.patches = [patch.object(core, 'PLUGIN_DIR', self.folder),
                        patch.object(builder, 'PLUGIN_DIR', self.folder),
                        patch.object(core, 'SETTINGS_PATH', self.folder / 'settings.json'),
                        patch.object(core.messagebox, 'showerror'),
                        patch.object(core.messagebox, 'showinfo'),
                        patch.object(core.messagebox, 'askyesnocancel', return_value=False)]
        for item in self.patches:
            item.start()
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.editor = core.Editor(self.root, False)
        self.root.update()
        self.api = core.PluginAPI(self.editor, 'plugin_builder')
        builder.register(self.api)
        self.editor.plugin_apis['plugin_builder'] = self.api
        self.api._commands[0][1]()
        self.root.update()
        self.window = next(widget for widget in widgets(self.root)
                           if isinstance(widget, tk.Toplevel) and widget.title() == 'Plugin Builder')

    def tearDown(self):
        try:
            self.editor.quit()
            self.assertEqual(self.errors, [])
        finally:
            for item in reversed(self.patches):
                item.stop()
            self.temp.cleanup()

    def button(self, label):
        return next(widget for widget in widgets(self.window)
                    if isinstance(widget, ttk.Button) and widget.cget('text') == label)

    def test_gui_create_load_preview_docs_and_duplicate(self):
        texts = [widget for widget in widgets(self.window) if isinstance(widget, tk.Text)]
        self.assertIn('def register(api):', texts[0].get('1.0', 'end'))
        self.assertIn('Public PluginAPI reference', texts[1].get('1.0', 'end'))
        self.assertIn('AVAILABLE SERVICES', texts[2].get('1.0', 'end'))
        self.button('Create & load').invoke(); self.root.update()
        path = self.folder / 'my_plugin.py'
        self.assertTrue(path.exists())
        self.assertIn(path, self.editor.loaded_plugins)
        self.assertEqual(self.editor.path, path)
        self.assertIn('my_plugin', self.editor.plugin_apis)
        before = path.read_bytes()
        self.button('Create & load').invoke(); self.root.update()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.editor.documents), 1)
        self.button('Open documentation in editor').invoke(); self.root.update()
        self.assertEqual(self.editor.path, builder.DOC_PATH)

    def test_generated_plugins_communicate_and_transform_active_tab(self):
        for plugin_id in ('counter', 'observer'):
            builder.create_plugin(self.folder, plugin_id, plugin_id.title(), '', 'Events and services')
        builder.create_plugin(self.folder, 'uppercase', 'Uppercase', '', 'Text transform')
        self.editor.load_plugins()
        self.assertIn('counter.word_count', self.api.list_services())
        counter = self.editor.plugin_apis['counter']
        observer = self.editor.plugin_apis['observer']
        received = []
        unsubscribe = observer.on('counter.counted', lambda **payload: received.append(payload))
        self.editor.buffer.insert('1.0', 'hello world')
        self.assertEqual(observer.call_service('counter.word_count'), 2)
        next(callback for key, label, callback in self.editor.plugin_commands if key == 'counter:Counter')()
        self.root.update()
        self.assertEqual(received, [{'source': 'counter', 'count': 2}])
        unsubscribe()
        self.editor.new(); self.editor.buffer.insert('1.0', 'second tab')
        self.assertEqual(counter.call_service('observer.word_count'), 2)
        next(callback for key, label, callback in self.editor.plugin_commands if key == 'uppercase:Uppercase')()
        self.assertEqual(self.api.get_text(), 'SECOND TAB')
        self.editor.buffer.edit_undo()
        self.assertEqual(self.api.get_text(), 'second tab')
        self.editor.select_document(self.editor.documents[0])
        self.assertEqual(self.api.get_text(), 'hello world')
        with self.assertRaises(LookupError):
            observer.call_service('missing.word_count')
        self.button('Refresh services').invoke()
        communication = [widget for widget in widgets(self.window) if isinstance(widget, tk.Text)][2]
        self.assertIn('counter.word_count', communication.get('1.0', 'end'))


if __name__ == '__main__':
    unittest.main()
