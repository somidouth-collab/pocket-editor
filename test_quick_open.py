"""File discovery and real Tk quick-open integration tests."""
from pathlib import Path
import queue
import threading
import time
import unittest
from unittest.mock import patch

import pocket_editor as core
import test_command_palette as fixtures
from quick_open import scan_files


class QuickOpenTests(unittest.TestCase):
    setUp = fixtures.PaletteTests.setUp
    tearDown = fixtures.PaletteTests.tearDown

    def wait_scan(self):
        end = time.monotonic() + 5
        while self.editor.quick_open.scanning and time.monotonic() < end:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.editor.quick_open.scanning)

    def test_search_relative_paths_open_and_existing_tab(self):
        folder = core.PLUGIN_DIR
        for name in ('src', 'tests', 'node_modules'):
            (folder / name).mkdir()
            (folder / name / 'sample.py').write_text(name)
        self.editor.open_path(folder / 'src/sample.py')
        provider = core.PluginAPI(self.editor, 'project_explorer')
        provider.provide_service('get_project', lambda: folder)
        self.editor.plugin_apis['project_explorer'] = provider
        self.editor.quick_open.show(); self.wait_scan()
        quick = self.editor.quick_open
        quick.query.set('SAMPLE.PY')
        self.assertEqual({item[0] for item in quick.matches}, {'src/sample.py', 'tests/sample.py'})
        quick.query.set('tests sample')
        quick.run(); self.root.update()
        self.assertEqual(self.editor.path, folder / 'tests/sample.py')
        self.assertEqual(len(self.editor.documents), 2)
        quick.show(); self.wait_scan(); quick.query.set('src sample'); quick.run()
        self.assertEqual(self.editor.path, folder / 'src/sample.py')
        self.assertEqual(len(self.editor.documents), 2)

    def test_choose_folder_cancel_and_close_cleanup(self):
        quick = self.editor.quick_open
        with patch('quick_open.filedialog.askdirectory', return_value=''):
            quick.show()
        self.assertIsNone(quick.window)
        path = core.PLUGIN_DIR / 'one.txt'; path.write_text('one')
        with patch('quick_open.filedialog.askdirectory', return_value=str(core.PLUGIN_DIR)):
            quick.show()
        quick.close()
        self.assertTrue(quick.canceled.is_set())
        self.assertIsNone(quick.poll_job)
        quick.show(); self.wait_scan()
        quick.query.set('nothing-matches')
        self.assertEqual(quick.matches, [])
        quick.run()
        self.assertIsNotNone(quick.window)
        quick.close()

    def test_scan_limits_and_cancellation(self):
        for number in range(5):
            (core.PLUGIN_DIR / f'{number}.txt').write_text(str(number))
        output = queue.Queue()
        canceled = threading.Event()
        scan_files(core.PLUGIN_DIR, canceled, output, limit=2)
        files = []
        while not output.empty():
            kind, value = output.get_nowait()
            if kind == 'files':
                files.extend(value)
            else:
                self.assertTrue(value[0])
        self.assertEqual(len(files), 2)
        canceled.set()
        scan_files(core.PLUGIN_DIR, canceled, output)
        self.assertTrue(output.empty())
