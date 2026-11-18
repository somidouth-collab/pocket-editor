"""Real loopback chat, persistence, transfer protection, and Tk integration tests."""
import gc
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pocket_editor_plugins._team_chat import ChatClient, ChatServer, ChatStore, MAX_FILE
from pocket_editor_plugins._team_chat_ui import ChatPane

TOKEN = 'local-test-token-at-least-16-characters'


class ChatTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='pocket-chat-test-')
        self.folder = Path(self.temp.name)
        self.server = ChatServer(self.folder / 'host', TOKEN, port=0)
        self.address = f'http://127.0.0.1:{self.server.port}'
        self.alice = ChatClient(self.address, TOKEN, 'project', 'Alice')
        self.bob = ChatClient(self.address, TOKEN, 'project', 'Bob')

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def test_two_teammates_messages_cursors_and_room_isolation(self):
        self.alice.send('Hello équipe!\nReady to work?')
        rows = self.bob.messages()
        self.assertEqual(rows[0]['author'], 'Alice')
        self.assertEqual(rows[0]['text'], 'Hello équipe!\nReady to work?')
        self.bob.send('Ready')
        self.assertEqual([row['text'] for row in self.alice.messages(rows[0]['id'])], ['Ready'])
        other = ChatClient(self.address, TOKEN, 'other-project', 'Charlie')
        self.assertEqual(other.messages(), [])

    def test_audio_video_and_document_transfers(self):
        for name, content in [('audio.wav', b'RIFF\x00\xffaudio'), ('video.mp4', b'\x00\x00video'),
                              ('notes café.txt', 'Team notes'.encode()), ('empty.bin', b'')]:
            with self.subTest(name=name):
                source = self.folder / name
                source.write_bytes(content)
                self.alice.upload(source)
                item = self.bob.messages()[-1]
                destination = self.folder / ('download-' + name)
                self.bob.download(item, destination)
                self.assertEqual(destination.read_bytes(), content)
                self.assertEqual(item['sha256'], hashlib.sha256(content).hexdigest())

    def test_invalid_token_cannot_read_write_or_download(self):
        wrong = ChatClient(self.address, 'incorrect-access-token', 'project', 'Stranger')
        with self.assertRaisesRegex(RuntimeError, 'key'):
            wrong.messages()
        with self.assertRaises(RuntimeError):
            wrong.send('not authorized')
        self.assertEqual(self.alice.messages(), [])
        request = Request(self.address + '/messages?room=project')
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 401)

    def test_history_and_files_survive_host_restart(self):
        source = self.folder / 'saved.bin'
        source.write_bytes(b'persistent')
        self.alice.send('Retained message')
        self.alice.upload(source)
        self.server.close()
        self.server = ChatServer(self.folder / 'host', TOKEN, port=0)
        client = ChatClient(f'http://127.0.0.1:{self.server.port}', TOKEN, 'project', 'Bob')
        rows = client.messages()
        self.assertEqual(rows[0]['text'], 'Retained message')
        client.download(rows[1], self.folder / 'restored.bin')
        self.assertEqual((self.folder / 'restored.bin').read_bytes(), b'persistent')

    def test_room_cannot_download_another_rooms_attachment(self):
        source = self.folder / 'secret.txt'
        source.write_text('room one')
        self.alice.upload(source)
        item = self.alice.messages()[0]
        other = ChatClient(self.address, TOKEN, 'other', 'Bob')
        with self.assertRaises(RuntimeError):
            other.download(item, self.folder / 'no-file')
        self.assertFalse((self.folder / 'no-file').exists())

    def test_failed_integrity_check_preserves_destination(self):
        source = self.folder / 'data.bin'
        source.write_bytes(b'original')
        self.alice.upload(source)
        item = self.bob.messages()[0]
        (self.folder / 'host/files' / item['file_id']).write_bytes(b'tampered')
        destination = self.folder / 'keep.bin'
        destination.write_bytes(b'keep me')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.bob.download(item, destination)
        self.assertEqual(destination.read_bytes(), b'keep me')
        self.assertEqual(list(self.folder.glob('.team-download-*')), [])

    def test_invalid_names_traversal_and_size_limits(self):
        store = ChatStore(self.folder / 'direct')
        for name in ('../escape', '..', 'sub/file', 'sub\\file', 'bad\nname'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                store.upload('project', 'Alice', name, io.BytesIO(b'x'), 1)
        with self.assertRaises(ValueError):
            store.upload('project', 'Alice', 'large.bin', io.BytesIO(), MAX_FILE + 1)
        with self.assertRaises(ValueError):
            store.upload('project', 'Alice', 'partial.bin', io.BytesIO(b'x'), 2)
        self.assertEqual(list(store.files.iterdir()), [])
        with self.assertRaises(ValueError):
            store.attachment('project', '../chat.sqlite3')
        with self.assertRaises(ValueError):
            self.alice.send('x' * 8001)

    def test_public_http_and_credential_urls_rejected(self):
        for address in ('http://8.8.8.8:8765', 'http://user:pass@localhost:8765', 'file:///tmp/chat',
                        'http://localhost:8765?token=secret'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                ChatClient(address, TOKEN, 'project', 'Alice')

    def test_oversized_request_rejected_before_body_read(self):
        request = Request(self.address + '/files?room=project&author=Alice&name=large.bin',
                          data=b'', headers={'Authorization': 'Bearer ' + TOKEN,
                                             'Content-Length': str(MAX_FILE + 1)})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 400)


class ChatGuiTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='pocket-chat-gui-')
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.host = ChatPane(self.root, self.root, self.folder / 'host')
        self.host.pack(fill='both', expand=True)
        self.guest = ChatPane(self.root, self.root, self.folder / 'guest')
        self.guest.pack(fill='both', expand=True)
        self.host.author.set('Alice')
        self.host.port.set('0')
        self.host.connect(True)
        self.until(lambda: self.host.connected)
        self.guest.address.set(self.host.address.get())
        self.guest.token.set(self.host.token.get())
        self.guest.author.set('Bob')
        self.guest.connect()
        self.until(lambda: self.guest.connected)

    def tearDown(self):
        try:
            self.host.cleanup()
            self.guest.cleanup()
            self.host.worker.thread.join(timeout=3)
            self.guest.worker.thread.join(timeout=3)
            self.root.destroy()
            self.assertEqual(self.errors, [])
        finally:
            self.temp.cleanup()

    def until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail('Timed out: ' + self.host.status.get() + ' / ' + self.guest.status.get())

    def test_send_receive_attach_download_and_leave(self):
        self.host.composer.insert('1.0', 'Hello from GUI')
        self.host.send()
        self.until(lambda: 'Hello from GUI' in self.guest.history.get('1.0', 'end'))
        self.assertEqual(self.host.composer.get('1.0', 'end-1c'), '')
        source = self.folder / 'clip.mp4'
        source.write_bytes(b'test video bytes')
        with patch('pocket_editor_plugins._team_chat_ui.filedialog.askopenfilename', return_value=str(source)):
            self.host.attach()
        self.until(lambda: bool(self.guest.attachments))
        self.guest.files.selection_set(next(iter(self.guest.attachments)))
        destination = self.folder / 'received.mp4'
        with patch('pocket_editor_plugins._team_chat_ui.filedialog.asksaveasfilename', return_value=str(destination)):
            self.guest.download()
        self.until(lambda: destination.exists() and not self.guest.transferring)
        self.assertEqual(destination.read_bytes(), source.read_bytes())
        self.guest.leave()
        self.until(lambda: not self.guest.connected and not self.guest.connecting)
        self.assertEqual(str(self.guest.send_button.cget('state')), 'disabled')

    def test_failed_send_keeps_draft(self):
        self.host.composer.insert('1.0', 'Draft to keep')
        with patch('pocket_editor_plugins._team_chat.ChatClient.send', side_effect=RuntimeError('offline')):
            self.host.send()
            self.until(lambda: not self.host.transferring)
        self.assertEqual(self.host.composer.get('1.0', 'end-1c'), 'Draft to keep')
        self.assertIn('offline', self.host.status.get())
