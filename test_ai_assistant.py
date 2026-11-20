"""Offline example-plugin tests. No real provider requests or API credits."""
from io import BytesIO
import json
import time
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError

import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins import ai_assistant as example


class ProtocolTests(unittest.TestCase):
    def config(self):
        return example.validate_config(dict(example.DEFAULTS, model='example-model'))

    def test_config_limits_transport_and_input_budget(self):
        config = self.config()
        for endpoint in ('http://remote.example/v1/chat/completions', 'https://user:secret@example.com/x',
                         'https://example.com/x?key=secret'):
            with self.assertRaises(ValueError):
                example.validate_config(dict(config, endpoint=endpoint))
        with self.assertRaises(ValueError):
            example.validate_config(dict(config, reply_tokens=0))
        with self.assertRaises(ValueError):
            example.build_messages(dict(config, input_bytes=1024), 'Chat', 'é' * 1000)
        messages = example.build_messages(config, 'Code', 'Review this', 'x = 1')
        self.assertIn('x = 1', messages[-1]['content'])
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({'choices': [{'message': {'content': 'Example reply'}}],
                                                'usage': {'total_tokens': 20}}).encode()
        opener = Mock(); opener.open.return_value = response
        with patch.object(example, 'build_opener', return_value=opener):
            reply, usage = example.send_request(config, 'secret-key', messages)
        self.assertEqual(reply, 'Example reply'); self.assertEqual(usage['total_tokens'], 20)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-key')
        self.assertEqual(json.loads(request.data)['max_tokens'], 1024)
        self.assertIsNone(example.NoRedirects().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_responses_payload_text_parts_usage_and_incomplete(self):
        config = example.validate_config(dict(self.config(), protocol='OpenAI Responses',
            endpoint='https://api.openai.com/v1/responses', reasoning='low'))
        messages = example.build_messages(config, 'Code', 'Review this')
        response = {'status': 'incomplete', 'output': [
            {'type': 'reasoning', 'summary': []},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Part one'},
                                          {'type': 'output_text', 'text': 'Part two'}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 15, 'total_tokens': 25}}
        with patch.object(example, 'request_json', return_value=response) as request:
            reply, usage = example.send_request(config, 'key', messages)
        self.assertEqual(reply, 'Part one\nPart two')
        payload = request.call_args.args[3]
        self.assertEqual(payload['input'], messages[1:])
        self.assertEqual(payload['instructions'], messages[0]['content'])
        self.assertEqual(payload['max_output_tokens'], 1024)
        self.assertEqual(payload['reasoning'], {'effort': 'low'})
        self.assertFalse(payload['store'])
        self.assertNotIn('max_tokens', payload)
        self.assertEqual(usage['total_tokens'], 25); self.assertTrue(usage['incomplete'])
        with patch.object(example, 'request_json', return_value={'status': 'incomplete', 'output': []}), self.assertRaises(example.AIResponseError):
            example.send_request(config, 'key', messages)

    def test_model_discovery_uses_get_and_preserves_origin(self):
        config = example.validate_config(dict(self.config(), protocol='OpenAI Responses', endpoint='https://api.openai.com/v1/responses'))
        with patch.object(example, 'request_json', return_value={'data': [{'id': 'model-b'}, {'id': 'model-a'}, {'id': 'model-a'}]}) as request:
            self.assertEqual(example.list_models(config, 'secret'), ['model-a', 'model-b'])
        self.assertEqual(request.call_args.args[2], 'https://api.openai.com/v1/models')
        self.assertEqual(len(request.call_args.args), 3)
        with self.assertRaises(ValueError):
            example.validate_config(dict(config, endpoint='https://api.openai.com/v1/chat/completions'))


class ExampleGuiTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.settings_patch = patch.object(example, 'SETTINGS_PATH', core.PLUGIN_DIR / 'example-settings.json')
        self.settings_patch.start()
        self.api = core.PluginAPI(self.editor, 'ai_assistant')
        self.app = example.AIAssistant(self.api)
        self.editor.plugin_apis['ai_assistant'] = self.api
        self.app.show()
        self.app.values['model'].set('example-model')
        self.root.update()

    def tearDown(self):
        try:
            fixtures.PaletteTests.tearDown(self)
        finally:
            self.settings_patch.stop()

    def wait_done(self):
        end = time.monotonic() + 5
        while self.app.busy and time.monotonic() < end:
            self.root.update(); time.sleep(.01)
        self.root.update()
        self.assertFalse(self.app.busy)

    def test_reply_event_settings_key_and_new_tab(self):
        self.app.key.set('never-save-this-key')
        self.app.save_settings()
        saved = example.SETTINGS_PATH.read_text()
        self.assertNotIn('never-save', saved); self.assertNotIn('key', json.loads(saved))
        received = []
        self.api.on('ai_assistant.reply', lambda **event: received.append(event))
        self.editor.buffer.insert('1.0', 'unsaved source')
        self.app.attach.set(True); self.app.mode.set('Code')
        self.app.prompt.insert('1.0', 'Write an example')
        with patch.object(example, 'send_request', return_value=('print("hello")', {'total_tokens': 12})) as send:
            self.app.submit(); self.wait_done()
        self.assertIn('unsaved source', send.call_args.args[2][-1]['content'])
        self.assertEqual(received[0]['text'], 'print("hello")')
        self.assertEqual(self.api.call_service('ai_assistant.get_status')['requests_used'], 1)
        self.app.open_reply()
        self.assertEqual(len(self.editor.documents), 2)
        self.assertEqual(self.api.get_text(), 'print("hello")')
        self.assertEqual(self.editor.documents[0]['widget'].get('1.0', 'end-1c'), 'unsaved source')
        self.app.reset(); self.assertEqual(self.app.used, 1)

    def test_file_attachments_snapshots_dedup_removal_and_budget(self):
        first = core.PLUGIN_DIR / 'first.py'; first.write_text('original source')
        second = core.PLUGIN_DIR / 'second.md'; second.write_text('project notes')
        with patch.object(example.filedialog, 'askopenfilenames', return_value=(str(first), str(second), str(first))):
            self.app.attach_files()
        self.assertEqual(len(self.app.attachments), 2)
        self.assertEqual(len(self.app.attachment_buttons), 2)
        self.assertEqual(self.app.used, 0)
        first.write_text('modified after attaching')
        self.app.prompt.insert('1.0', 'Review these files')
        with patch.object(example, 'send_request', return_value=('Reviewed', {})) as send:
            self.app.submit(); self.wait_done()
        context = send.call_args.args[2][-1]['content']
        self.assertIn('original source', context)
        self.assertIn('project notes', context)
        self.assertNotIn('modified after attaching', context)
        self.assertNotIn('original source', self.app.output.get('1.0', 'end-1c'))
        self.app.attachment_buttons[0].invoke()
        self.assertEqual([item['path'] for item in self.app.attachments], [second])
        before = list(self.app.attachments)
        bad = core.PLUGIN_DIR / 'bad.bin'; bad.write_bytes(b'bad\x00binary')
        with patch.object(example.filedialog, 'askopenfilenames', return_value=(str(bad),)), patch.object(example.messagebox, 'showerror') as error:
            self.app.attach_files(); error.assert_called_once()
        self.assertEqual(self.app.attachments, before)
        paths = []
        for index in range(5):
            path = core.PLUGIN_DIR / f'file-{index}.txt'; path.write_text('small'); paths.append(str(path))
        with patch.object(example.filedialog, 'askopenfilenames', return_value=paths), patch.object(example.messagebox, 'showerror'):
            self.app.attach_files()
        self.assertEqual(self.app.attachments, before)
        self.app.reset()
        self.app.values['input_bytes'].set('1024')
        self.app.prompt.insert('1.0', 'x' * 1100)
        with patch.object(example.messagebox, 'showerror'), patch.object(example, 'send_request') as send:
            self.app.submit(); send.assert_not_called()
        self.assertEqual(self.app.prompt.get('1.0', 'end-1c'), 'x' * 1100)
        self.assertEqual(self.app.attachments, before)

    def test_modern_layout_settings_and_message_copy(self):
        self.assertEqual(self.app.settings_window.state(), 'withdrawn')
        self.app.show_settings(); self.root.update()
        self.assertTrue(self.app.settings_window.winfo_ismapped())
        self.app.settings_window.withdraw()
        raw = 'Assistant:\nExample:\n```python\nprint("hello")\n```\n\n'
        self.app.append(raw)
        self.assertTrue(self.app.output.tag_ranges('role'))
        self.assertTrue(self.app.output.tag_ranges('code'))
        self.assertFalse(self.app.welcome.winfo_manager())
        self.app.copy_conversation()
        self.assertEqual(self.root.clipboard_get(), raw)
        for width, height in ((640, 540), (1100, 800)):
            self.app.window.geometry(f'{width}x{height}'); self.root.update()
            self.assertTrue(self.app.send.winfo_ismapped())
            self.assertTrue(self.app.prompt.winfo_ismapped())
            self.assertLessEqual(self.app.send.winfo_rootx() + self.app.send.winfo_width(),
                                 self.app.window.winfo_rootx() + self.app.window.winfo_width())
            self.assertLessEqual(self.app.send.winfo_rooty() + self.app.send.winfo_height(),
                                 self.app.window.winfo_rooty() + self.app.window.winfo_height())
        self.app.hide(); self.root.update()
        self.assertFalse(self.app.window.winfo_ismapped())
        self.app.show(); self.root.update()
        self.assertTrue(self.app.window.winfo_ismapped())
        self.assertEqual(self.app.output.get('1.0', 'end-1c'), raw)

    def test_keyboard_send_and_examples_do_not_send_automatically(self):
        with patch.object(self.app, 'submit') as submit:
            self.app.use_example('Build a plugin')
            self.assertEqual(self.app.prompt.get('1.0', 'end-1c'), 'Build a plugin')
            submit.assert_not_called()
            self.assertIsNone(self.app.send_enter(Mock(state=1)))
            submit.assert_not_called()
            self.assertEqual(self.app.send_enter(Mock(state=0)), 'break')
            submit.assert_called_once()

    def test_openai_preset_model_refresh_and_missing_key(self):
        self.app.key.set('old-provider-key')
        with patch.dict(example.os.environ, {'OPENAI_API_KEY': 'openai-test-key'}):
            self.app.provider.set('OpenAI'); self.app.apply_preset()
        self.assertEqual(self.app.key.get(), 'openai-test-key')
        self.assertEqual(self.app.values['protocol'].get(), 'OpenAI Responses')
        self.assertEqual(self.app.values['endpoint'].get(), 'https://api.openai.com/v1/responses')
        with patch.object(example, 'list_models', return_value=['account-model']) as listing:
            self.app.refresh_models(); self.wait_done()
        listing.assert_called_once()
        self.assertIn('account-model', self.app.model_picker.cget('values'))
        self.assertEqual(self.app.used, 1)
        self.app.values['model'].set('account-model')
        self.app.key.set('')
        self.app.prompt.insert('1.0', 'Do not send without a key')
        with patch.object(example.messagebox, 'showerror') as error, patch.object(example, 'send_request') as send:
            self.app.submit()
            send.assert_not_called(); error.assert_called_once()
        self.assertIn('Do not send', self.app.prompt.get('1.0', 'end-1c'))
        self.assertEqual(self.app.used, 1)
        self.app.key.set('not-persisted')
        self.app.save_settings()
        settings = json.loads(example.SETTINGS_PATH.read_text())
        self.assertEqual(settings['protocol'], 'OpenAI Responses')
        self.assertNotIn('not-persisted', example.SETTINGS_PATH.read_text())

    def test_rate_limit_no_retry_and_request_cap(self):
        self.app.prompt.insert('1.0', 'Hello')
        error = HTTPError('https://example.com', 429, 'rate limit', {'Retry-After': '2'}, BytesIO())
        with patch.object(example, 'send_request', side_effect=error) as send:
            self.app.submit(); self.wait_done()
            self.assertEqual(send.call_count, 1)
        self.assertGreater(self.app.pause_until, time.monotonic())
        self.assertEqual(self.app.prompt.get('1.0', 'end-1c'), 'Hello')
        self.app.prompt.delete('1.0', 'end')
        self.app.prompt.insert('1.0', 'preserve this')
        with patch.object(example.messagebox, 'showerror'):
            self.app.submit()
        self.assertEqual(self.app.prompt.get('1.0', 'end-1c'), 'preserve this')
        self.app.pause_until = 0
        self.app.values['session_requests'].set('1')
        with patch.object(example.messagebox, 'showerror'), patch.object(example, 'send_request') as send:
            self.app.submit()
            send.assert_not_called()
        self.assertEqual(self.app.used, 1)

    def test_gemini_preset_switch_key_isolation_models_and_help(self):
        self.app.provider.set('OpenAI'); self.app.apply_preset()
        self.app.key.set('openai-private')
        with patch.dict(example.os.environ, {'GEMINI_API_KEY': 'google-private'}):
            self.app.provider.set('Google Gemini'); self.app.apply_preset()
        self.assertEqual(self.app.key.get(), 'google-private')
        self.assertEqual(self.app.values['protocol'].get(), 'Google Gemini')
        self.assertEqual(self.app.values['endpoint'].get(), 'https://generativelanguage.googleapis.com/v1beta')
        with patch.object(example, 'list_models', return_value=['gemini-test']) as listing:
            self.app.refresh_models(); self.wait_done()
        self.assertEqual(listing.call_args.args[1], 'google-private')
        self.assertIn('gemini-test', self.app.model_picker.cget('values'))
        self.app.values['model'].set('gemini-test')
        self.app.save_settings()
        saved = example.SETTINGS_PATH.read_text()
        self.assertNotIn('google-private', saved)
        self.assertNotIn('openai-private', saved)
        self.app.provider.set('OpenAI'); self.app.apply_preset()
        self.assertEqual(self.app.key.get(), 'openai-private')
        self.app.provider.set('Google Gemini'); self.app.apply_preset()
        self.assertEqual(self.app.values['model'].get(), 'gemini-test')
        self.app.values['endpoint'].set('https://other-provider.example/v1beta')
        self.assertEqual(self.app.key.get(), '')
        self.app.show_help()
        self.root.update()
        self.assertTrue(any('Gemini example' in w.title() for w in self.app.window.winfo_children() if isinstance(w, example.tk.Toplevel)))

    def test_404_repair_clears_unavailable_model_without_generation_retry(self):
        self.app.provider.set('Google Gemini'); self.app.apply_preset()
        self.app.key.set('private-google-key')
        self.app.values['model'].set('gemini-2.5-flash-lite')
        self.app.prompt.insert('1.0','Hello Gemini')
        error = HTTPError('https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent',404,'not found',{},
                          BytesIO(json.dumps({'error':{'message':'Model not available. api_key=private-google-key'}}).encode()))
        with patch.object(example,'send_request',side_effect=error) as send:
            self.app.submit();self.wait_done();self.assertEqual(send.call_count,1)
        self.assertTrue(self.app.repair_button.winfo_ismapped())
        self.assertIn('gemini-2.5-flash-lite',self.app.status.cget('text'))
        self.assertNotIn('private-google-key',self.app.output.get('1.0','end-1c'))
        self.assertEqual(self.app.prompt.get('1.0','end-1c'),'Hello Gemini')
        with patch.object(example,'list_models',return_value=['gemini-available']) as listing, patch.object(example,'send_request') as send:
            self.app.repair_connection();self.wait_done()
            listing.assert_called_once();send.assert_not_called()
        self.assertEqual(self.app.values['model'].get(),'')
        self.assertIn('gemini-available',self.app.model_picker.cget('values'))
        self.assertIn('not in the available',self.app.settings_status.cget('text'))


class GeminiTests(unittest.TestCase):
    def config(self):
        protocol, endpoint = example.PRESETS['Google Gemini']
        return example.validate_config(dict(example.DEFAULTS, protocol=protocol, endpoint=endpoint, model='models/gemini-test'))

    def test_native_gemini_payload_history_usage_and_authentication(self):
        config = self.config()
        messages = example.build_messages(config, 'Chat', 'Hello', history=[
            {'role': 'user', 'content': 'previous'}, {'role': 'assistant', 'content': 'answer'}])
        result = {'candidates': [{'content': {'parts': [{'text': 'private reasoning', 'thought': True}, {'text': 'Hello back'}]}, 'finishReason': 'STOP'}],
                  'usageMetadata': {'totalTokenCount': 23}}
        with patch.object(example, 'request_json', return_value=result) as request:
            reply, usage = example.send_request(config, 'gemini-secret', messages)
        self.assertEqual(reply, 'Hello back')
        self.assertEqual(usage['total_tokens'], 23)
        self.assertFalse(usage['incomplete'])
        self.assertEqual(request.call_args.args[2], 'https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent')
        payload = request.call_args.args[3]
        self.assertEqual(payload['contents'][1]['role'], 'model')
        self.assertEqual(payload['generationConfig']['maxOutputTokens'], 1024)
        self.assertNotIn('messages', payload)
        self.assertNotIn('model', payload)
        response = Mock()
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(result).encode()
        opener = Mock(); opener.open.return_value = response
        with patch.object(example, 'build_opener', return_value=opener):
            example.request_json(config, 'gemini-secret', request.call_args.args[2], payload)
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.get_header('X-goog-api-key'), 'gemini-secret')
        self.assertIsNone(sent.get_header('Authorization'))
        self.assertNotIn('gemini-secret', sent.full_url)

    def test_catalog_filters_embedding_models_and_refusals_are_explicit(self):
        config = self.config()
        with patch.object(example, 'request_json', return_value={'models': [
                {'name': 'models/gemini-test', 'supportedGenerationMethods': ['generateContent']},
                {'name': 'models/embedding-only', 'supportedGenerationMethods': ['embedContent']}]}):
            self.assertEqual(example.list_models(config, 'key'), ['gemini-test'])
        for response in ({'promptFeedback': {'blockReason': 'SAFETY'}},
                         {'candidates': [{'content': {'parts': []}, 'finishReason': 'MAX_TOKENS'}]}):
            with patch.object(example, 'request_json', return_value=response), self.assertRaises(example.AIResponseError):
                example.send_request(config, 'key', example.build_messages(config, 'Chat', 'hello'))
        with self.assertRaises(ValueError):
            example.validate_config(dict(config, model='../other:generateContent'))

    def test_native_gemini_http_roundtrip(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                seen.append((self.path, self.headers.get('x-goog-api-key')))
                self.respond({'models': [{'name': 'models/gemini-test', 'supportedGenerationMethods': ['generateContent']}]})
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((self.path, self.headers.get('x-goog-api-key'), payload))
                self.respond({'candidates': [{'content': {'parts': [{'text': 'Local Gemini test reply'}]}, 'finishReason': 'STOP'}]})
            def respond(self, data):
                body = json.dumps(data).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            config = example.validate_config(dict(self.config(), endpoint=f'http://127.0.0.1:{server.server_port}/v1beta'))
            self.assertEqual(example.list_models(config, 'local-test-key'), ['gemini-test'])
            reply, _ = example.send_request(config, 'local-test-key', example.build_messages(config, 'Chat', 'hello'))
            self.assertEqual(reply, 'Local Gemini test reply')
            self.assertEqual(seen[0], ('/v1beta/models?pageSize=1000', 'local-test-key'))
            self.assertEqual(seen[1][0], '/v1beta/models/gemini-test:generateContent')
            self.assertEqual(seen[1][2]['contents'][0]['parts'][0]['text'], 'hello')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_catalog_uses_base_model_id(self):
        config=self.config()
        with patch.object(example,'request_json',return_value={'models':[
            {'name':'models/versioned-resource-001','baseModelId':'gemini-current',
             'supportedGenerationMethods':['generateContent']} ]}) as request:
            self.assertEqual(example.list_models(config,'key'),['gemini-current'])
        self.assertTrue(request.call_args.args[2].endswith('/models?pageSize=1000'))
