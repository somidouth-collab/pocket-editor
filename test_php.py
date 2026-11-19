"""Integration tests against real Prettier, Intelephense and real Tk widgets."""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import copy
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk

import test_editor
from pocket_editor_plugins._php_tools import (TOOLS, LanguageServer, completion_edits,
    offset_at, position_at, project_root)
from pocket_editor_plugins._php_formatter import Formatter
from pocket_editor_plugins._php_completion import (DEFAULTS, Capture, CompletionWorker,
    read_settings as read_completion_settings, server_config, render_snippet, prepare_completion, ranked_items)
from pocket_editor_plugins._php_tools import CompletionCancelled

DEMO = Path(__file__).parent / 'examples' / 'php'
AVAILABLE = shutil.which('node') and (TOOLS / 'node_modules' / 'intelephense').is_dir()


class ProtocolTests(unittest.TestCase):
    def test_utf16_positions_and_import_edits(self):
        source = '<?php\n// 😀 café\n$g = new Gree;'
        cursor = source.index('Gree') + 4
        for offset in (0, source.index('😀'), source.index('😀') + 1, cursor, len(source)):
            self.assertEqual(offset_at(source, position_at(source, offset)), offset)
        edits, caret = completion_edits(source, cursor, {'label': 'Greeter', 'textEdit': {
            'range': {'start': position_at(source, cursor-4), 'end': position_at(source, cursor)},
            'newText': 'Greeter'}, 'additionalTextEdits': [{'range': {
                'start': {'line': 1, 'character': 0}, 'end': {'line': 1, 'character': 0}},
                'newText': 'use PocketDemo\\Services\\Greeter;\n'}]})
        result = source
        for start, end, value, _ in reversed(edits):
            result = result[:start] + value + result[end:]
        self.assertIn('use PocketDemo\\Services\\Greeter;', result)
        self.assertEqual(result[caret-7:caret], 'Greeter')
        with self.assertRaises(ValueError):
            completion_edits(source, cursor, {'label': 'bad', 'insertTextFormat': 2})
        with self.assertRaises(ValueError):
            offset_at('😀', {'line': 0, 'character': 1})

    def test_root_and_overlapping_edits(self):
        self.assertEqual(project_root(DEMO/'src'/'Models'/'Customer.php'), DEMO.resolve())
        self.assertEqual(project_root(DEMO/'demo.php', Path('/tmp')), DEMO.resolve())
        with self.assertRaises(ValueError):
            completion_edits('test', 4, {'label':'next', 'additionalTextEdits': [{
                'range': {'start': {'line':0, 'character':1}, 'end': {'line':0, 'character':3}},
                'newText':'bad'}]})

    def test_snippet_defaults_choices_and_safe_edits(self):
        value, stops = render_snippet(r'call(${1:\$customer}, ${2|true,false|})$0')
        self.assertEqual(value, 'call($customer, true)')
        self.assertEqual([stop[0] for stop in stops], [1,2,0])
        self.assertEqual(value[stops[0][1]:stops[0][2]], '$customer')
        edits, caret, ranges = prepare_completion('<?php call',10,{
            'label':'call','insertTextFormat':2,'insertText':r'call(${1:\$customer})$0'})
        result = '<?php call'
        for start,end,value,_ in reversed(edits): result=result[:start]+value+result[end:]
        self.assertEqual(result,'<?php call($customer)')
        self.assertEqual(result[caret:caret+9], '$customer')
        with self.assertRaises(ValueError): render_snippet('${TM_FILENAME}')
        with self.assertRaises(ValueError): render_snippet('${1/foo/bar/}')

    def test_completion_settings_validation_and_ranking(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'settings.json'
            path.write_text('{"automatic": "yes", "delay_ms": true, "max_items": 9999, "projects": []}')
            self.assertEqual(read_completion_settings(path), DEFAULTS)
            path.write_text('{"automatic": false, "delay_ms": 80, "min_chars": 1, "max_items": 50}')
            self.assertEqual(read_completion_settings(path)['delay_ms'],80)
            self.assertFalse(read_completion_settings(path)['automatic'])
        items=ranked_items([{'label':'sillyDisplayName'},{'label':'displayName'},{'label':'DisplayLabel'}],
                           '<?php disp',10,100)
        self.assertEqual([item['label'] for item in items],['displayName','DisplayLabel','sillyDisplayName'])


@unittest.skipUnless(AVAILABLE, 'Install local PHP tools with npm ci --prefix tools/php --ignore-scripts')
class RealToolTests(unittest.TestCase):
    def test_formatter_idempotence_unicode_and_invalid_php(self):
        formatter = Formatter()
        try:
            source = '<?php\n// 😀 Unicode\n$name="é 😀";$values=[1,2,3];\necho $name;'
            cursor = source.index('$values')
            first = formatter.format(source, DEMO/'demo.php', cursor)
            self.assertIn('é 😀', first['formatted'])
            self.assertEqual(first['formatted'][first['cursorOffset']:].split('=',1)[0].strip(), '$values')
            second = formatter.format(first['formatted'], DEMO/'demo.php', first['cursorOffset'])
            self.assertEqual(first['formatted'], second['formatted'])
            with self.assertRaises(RuntimeError):
                formatter.format('<?php function broken( {', DEMO/'demo.php')
        finally:
            formatter.close()

    def test_language_server_real_use_alias_group_and_require_files(self):
        server = LanguageServer(DEMO)
        process = server.process
        def find(path, source, expected):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                items = server.complete(path, source, len(source))
                if expected in [item['label'] for item in items]:
                    return next(item for item in items if item['label'] == expected)
                time.sleep(.3)
            self.fail('Missing real completion: '+expected)
        try:
            source=(DEMO/'demo.php').read_text()
            find(DEMO/'demo.php', source+'\n$greeter->gre', 'greetCustomer')
            find(DEMO/'demo.php', source+'\n$customer->disp', 'displayName')
            find(DEMO/'demo.php', source+'\nWelcomeService::defaultG', 'defaultGreeting')
            find(DEMO/'demo.php', source+'\nformat_customer_l', 'format_customer_label')
            grouped=(DEMO/'grouped.php').read_text()
            find(DEMO/'grouped.php', grouped+'\n$greeter->gre', 'greetCustomer')
            included="<?php\nrequire_once __DIR__ . '/includes/legacy.php';\nlegacy_customer_l"
            find(DEMO/'included.php', included, 'legacy_customer_label')
        finally:
            server.close()
        self.assertIsNotNone(process.poll())

    def test_advanced_php_members_builtins_and_request_cancellation(self):
        server=LanguageServer(DEMO)
        source=(DEMO/'advanced_completion.php').read_text()
        cases=[('$repository->find','findCustomer'),('$repository->lab','label'),
               ('$repository->ident','identifier'),('$repository->magic','magicReport'),
               ('$repository->findCustomer()->disp','displayName'),('UserRepository::TAB','TABLE'),
               ('State::Re','Ready'),('$customers[0]->disp','displayName'),('str_rep','str_replace')]
        try:
            for prefix,expected in cases:
                document=source+'\n'+prefix
                deadline=time.monotonic()+15
                found=[]
                while time.monotonic()<deadline:
                    found=server.complete(DEMO/'advanced_completion.php',document,len(document))
                    if expected in [item['label'] for item in found]: break
                    time.sleep(.15)
                self.assertIn(expected,[item['label'] for item in found],prefix)
            with self.assertRaises(CompletionCancelled):
                server.complete(DEMO/'advanced_completion.php',document,len(document),cancel=lambda: True)
            found=server.complete(DEMO/'advanced_completion.php',document,len(document))
            self.assertIn('str_replace',[item['label'] for item in found])
        finally: server.close()

    def test_external_user_library_include_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'project'; root.mkdir()
            library=Path(temp)/'user-library'; library.mkdir()
            (library/'ReportClient.php').write_text('<?php namespace UserLibrary; class ReportClient { public function fetchReport(): string {return "report";} }')
            source='<?php\nuse UserLibrary\\ReportClient;\n$client = new ReportClient();\n$client->fetch'
            settings=copy.deepcopy(DEFAULTS)
            settings['projects'][str(root)]={'php_version':'8.4','include_paths':[str(library)]}
            server=LanguageServer(root,config=server_config(settings,root))
            try:
                deadline=time.monotonic()+15; found=[]
                while time.monotonic()<deadline:
                    found=server.complete(root/'user.php',source,len(source))
                    if 'fetchReport' in [item['label'] for item in found]: break
                    time.sleep(.2)
                self.assertIn('fetchReport',[item['label'] for item in found])
            finally: server.close()


@unittest.skipUnless(AVAILABLE, 'Local PHP tools are optional')
class PHPGuiTests(unittest.TestCase):
    def setUp(self):
        test_editor.EditorTests.setUp(self)
        api = self.editor.plugin_apis['php_tools']
        self.completion = next(callback.__self__ for label,callback in api._commands if label == 'PHP Completion')
        self.completion.settings_path = self.folder/'php-completion-settings.json'
        self.completion.settings = copy.deepcopy(DEFAULTS)
    tearDown = test_editor.EditorTests.tearDown
    pump = test_editor.EditorTests.pump
    until = test_editor.EditorTests.until
    command = test_editor.EditorTests.command
    window = test_editor.EditorTests.window

    def test_php_format_menu_undo_redo_and_disk_preservation(self):
        path=self.folder/'test.php'; original='<?php\n$values= [1,2,3];echo \"hello\";\n'; path.write_text(original)
        e=self.editor; e.open_path(path)
        self.command('Format PHP')
        self.until(lambda: not e.plugin_apis['php_tools'].call_service('php_tools.busy'))
        formatted=e.plugin_apis['php_tools'].get_text()
        self.assertNotEqual(formatted, original)
        self.assertEqual(path.read_text(), original)
        e.buffer.event_generate('<<Undo>>'); self.pump(); self.assertEqual(e.plugin_apis['php_tools'].get_text(), original)
        e.buffer.event_generate('<<Redo>>'); self.pump(); self.assertEqual(e.plugin_apis['php_tools'].get_text(), formatted)
        self.assertTrue(e.buffer.edit_modified())
        self.assertNotIn('php_tools:Format PHP', e.toolbar_commands)

    def test_stale_result_and_non_php_protection(self):
        e=self.editor; e.set_document(DEMO/'demo.php', (DEMO/'demo.php').read_text())
        self.command('Format PHP')
        e.set_document(self.folder/'new.txt', 'preserve new document')
        self.until(lambda: not e.plugin_apis['php_tools'].call_service('php_tools.busy'))
        self.assertEqual(e.plugin_apis['php_tools'].get_text(), 'preserve new document')
        self.command('Format PHP'); self.assertTrue(self.showinfo.called)
        self.command('PHP Completion')
        self.assertFalse(e.plugin_apis['php_tools'].call_service('php_tools.status')['busy'])

    def test_popup_completion_acceptance_undo_and_php_only(self):
        e=self.editor; original=(DEMO/'demo.php').read_text()+'\n$customer->disp'
        e.set_document(DEMO/'demo.php', original); e.buffer.mark_set('insert','end-1c')
        api=e.plugin_apis['php_tools']
        self.command('PHP Completion')
        self.until(lambda: not api.call_service('php_tools.status')['busy'], timeout=35)
        state=api.call_service('php_tools.status')
        self.assertFalse(state['error'], state['error'])
        self.assertIn('displayName',state['items'], str(state))
        if self.completion.worker.server.can_resolve:
            self.until(lambda: bool(self.completion.detail_cache))
            self.assertIn('Method', self.completion.description.cget('text'))
        listing=next(w for w in test_editor.widgets(self.root) if isinstance(w,tk.Listbox))
        index=list(listing.get(0,'end')).index('displayName')
        listing.selection_clear(0,'end'); listing.selection_set(index)
        e.buffer.event_generate('<Return>'); self.pump()
        self.until(lambda: not api.call_service('php_tools.status')['busy'])
        self.assertRegex(api.get_text(), r'\$customer->displayName(?:\(\))?$')
        e.buffer.event_generate('<<Undo>>'); self.pump(); self.assertEqual(api.get_text(),original)
        e.set_document(self.folder/'plain.py','displayName')
        e.buffer.event_generate('<Return>'); self.pump()
        self.assertEqual(api.get_text(),'\ndisplayName')
        self.assertFalse(listing.winfo_viewable())
        pid = api.call_service('php_tools.status')['server_pid']
        e.buffer.edit_modified(False); e.quit()
        if os.name == 'posix' and Path('/proc').is_dir():
            deadline = time.monotonic() + 3
            while Path(f'/proc/{pid}').exists() and time.monotonic() < deadline:
                time.sleep(.03)
            self.assertFalse(Path(f'/proc/{pid}').exists(), 'Language server survived editor exit')

    def test_saved_imported_file_refreshes_completion(self):
        project = self.folder / 'php-project'
        shutil.copytree(DEMO, project)
        e = self.editor
        api = e.plugin_apis['php_tools']
        original = (project/'demo.php').read_text()
        e.set_document(project/'demo.php', original + '\n$greeter->gre')
        e.buffer.mark_set('insert', 'end-1c')
        self.command('PHP Completion')
        self.until(lambda: not api.call_service('php_tools.status')['busy'], timeout=35)
        self.assertIn('greetCustomer', api.call_service('php_tools.status')['items'], str(api.call_service('php_tools.status')))
        imported = project/'src'/'Services'/'Greeter.php'
        e.set_document(imported, imported.read_text().replace('greetCustomer', 'saluteCustomer'))
        self.assertTrue(e.save()); self.pump()
        e.set_document(project/'demo.php', original + '\n$greeter->salu')
        e.buffer.mark_set('insert', 'end-1c')
        self.command('PHP Completion')
        self.until(lambda: not api.call_service('php_tools.status')['busy'], timeout=35)
        self.assertIn('saluteCustomer', api.call_service('php_tools.status')['items'])

    def test_completion_missing_runtime_and_invalid_format_leave_text(self):
        e=self.editor; e.set_document(self.folder/'broken.php','<?php function broken( {')
        self.command('Format PHP')
        self.until(lambda: not e.plugin_apis['php_tools'].call_service('php_tools.busy'))
        self.assertEqual(e.plugin_apis['php_tools'].get_text(),'<?php function broken( {')
        self.assertTrue(self.showerror.called)
        e.set_document(self.folder/'valid.php','<?php echo "ok";')
        with patch('pocket_editor_plugins._php_tools.shutil.which',return_value=None):
            self.command('Format PHP')
            self.until(lambda: not e.plugin_apis['php_tools'].call_service('php_tools.busy'))
        self.assertEqual(e.plugin_apis['php_tools'].get_text(),'<?php echo "ok";')
        with patch('pocket_editor_plugins._php_tools.shutil.which',return_value=None):
            self.command('Reindex PHP Project')
            self.until(lambda: not e.plugin_apis['php_tools'].call_service('php_tools.status')['busy'])
        self.assertIn('Node.js', e.plugin_apis['php_tools'].call_service('php_tools.status')['error'])
        self.assertEqual(e.plugin_apis['php_tools'].get_text(),'<?php echo "ok";')

    def test_escape_cancels_popup_and_exact_cache_is_used(self):
        e=self.editor; api=e.plugin_apis['php_tools']
        original=(DEMO/'demo.php').read_text()+'\n$customer->disp'
        e.set_document(DEMO/'demo.php',original); e.buffer.mark_set('insert','end-1c')
        self.command('PHP Completion')
        self.until(lambda:not api.call_service('php_tools.status')['busy'],timeout=35)
        self.assertIn('displayName',api.call_service('php_tools.status')['items'])
        self.command('PHP Completion')
        self.until(lambda:not api.call_service('php_tools.status')['busy'])
        self.assertGreaterEqual(api.call_service('php_tools.status')['cache_hits'],1)
        e.plugins.invoke('PHP Completion'); e.buffer.event_generate('<Escape>'); self.pump(.5)
        self.assertFalse(api.call_service('php_tools.status')['busy'])
        self.assertFalse(self.completion.popup.winfo_viewable())
        self.assertEqual(api.get_text(),original)

    def test_parameter_tabstops_and_undo(self):
        e=self.editor; original='<?php call'
        e.set_document(self.folder/'args.php',original); e.buffer.mark_set('insert','end-1c'); self.pump()
        controller=self.completion
        controller.capture=controller.capture_now()
        controller.apply({'label':'call','insertTextFormat':2,'insertText':r'call(${1:\$user}, ${2:42})$0'})
        self.pump()
        self.assertEqual(e.buffer.get('sel.first','sel.last'),'$user')
        e.buffer.event_generate('<Tab>'); self.pump()
        self.assertEqual(e.buffer.get('sel.first','sel.last'),'42')
        e.buffer.event_generate('<Shift-Tab>'); self.pump()
        self.assertEqual(e.buffer.get('sel.first','sel.last'),'$user')
        e.buffer.event_generate('<Escape>'); self.pump()
        e.buffer.event_generate('<<Undo>>'); self.pump()
        self.assertEqual(e.plugin_apis['php_tools'].get_text(),original)

    def test_project_settings_ui_persistence(self):
        e=self.editor; e.set_document(DEMO/'demo.php','<?php'); self.pump()
        self.command('PHP Completion Settings'); window=self.window('PHP Completion Settings')
        self.assertFalse(self.completion.settings_path.exists())
        spins=[w for w in test_editor.widgets(window) if isinstance(w,ttk.Spinbox)]
        spins[0].delete(0,'end'); spins[0].insert(0,'80')
        for widget in test_editor.widgets(window):
            if isinstance(widget,ttk.Checkbutton): widget.invoke()
        with patch('tkinter.filedialog.askdirectory',return_value=str(self.folder)):
            next(w for w in test_editor.widgets(window) if isinstance(w,ttk.Button) and w.cget('text')=='Add folder…').invoke()
        next(w for w in test_editor.widgets(window) if isinstance(w,ttk.Button) and w.cget('text')=='Save Settings').invoke()
        self.pump()
        saved=read_completion_settings(self.completion.settings_path)
        self.assertEqual(saved['delay_ms'],80)
        self.assertFalse(saved['automatic'])
        self.assertEqual(saved['projects'][str(DEMO.resolve())]['include_paths'],[str(self.folder)])

    def test_real_typing_and_deletion_auto_completion(self):
        e=self.editor; api=e.plugin_apis['php_tools']; controller=self.completion
        source=(DEMO/'demo.php').read_text()+'\n$customer->di'
        e.set_document(DEMO/'demo.php',source); e.buffer.mark_set('insert','end-1c')
        e.buffer.focus_force(); self.pump()
        released=[]
        binding=e.buffer.bind('<KeyRelease>',lambda event: released.append(event.char),add='+')
        def key(keysym):
            e.buffer.event_generate('<KeyPress>',keysym=keysym)
            e.buffer.event_generate('<KeyRelease>',keysym=keysym)
        try:
            key('s')
            self.assertTrue(api.get_text().endswith('$customer->dis'))
            self.assertEqual(released[-1], '')
            self.assertIsNotNone(controller.pending, 'Typing did not schedule completion')
            self.until(lambda: controller.pending is None and not api.call_service('php_tools.status')['busy']
                       and 'displayName' in api.call_service('php_tools.status')['items'],timeout=35)
            key('p')
            self.assertIsNotNone(controller.pending)
            self.until(lambda: controller.pending is None and not api.call_service('php_tools.status')['busy']
                       and 'displayName' in api.call_service('php_tools.status')['items'])
            key('BackSpace')
            self.assertTrue(api.get_text().endswith('$customer->dis'))
            self.assertIsNotNone(controller.pending)
            self.until(lambda: controller.pending is None and not api.call_service('php_tools.status')['busy']
                       and 'displayName' in api.call_service('php_tools.status')['items'])
            e.buffer.insert('end-1c','x'); e.buffer.mark_set('insert','end-2c'); self.pump()
            key('Delete')
            self.assertTrue(api.get_text().endswith('$customer->dis'))
            self.assertIsNotNone(controller.pending)
            self.until(lambda: controller.pending is None and not api.call_service('php_tools.status')['busy']
                       and 'displayName' in api.call_service('php_tools.status')['items'])
            key('Left')
            self.assertIsNone(controller.pending, 'Cursor movement must not schedule completion')
            self.assertFalse(controller.popup.winfo_viewable())
            e.buffer.mark_set('insert','end-1c')
            e.buffer.event_generate('<KeyPress>',keysym='space',state=4)
            e.buffer.event_generate('<KeyRelease>',keysym='space',state=4)
            self.assertIsNotNone(controller.ticket, 'Releasing Ctrl+Space cancelled its request')
            self.until(lambda: not api.call_service('php_tools.status')['busy']
                       and 'displayName' in api.call_service('php_tools.status')['items'])
            self.assertTrue(controller.popup.winfo_viewable())
        finally:
            e.buffer.unbind('<KeyRelease>',binding)
