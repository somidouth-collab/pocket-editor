"""Host/client project copies, individual keys, and concurrent edit conflicts."""
import gc
import hashlib
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest

from pocket_editor_plugins._team_chat import ChatClient, ChatServer
from pocket_editor_plugins._team_project_sync import ProjectCopy, ProjectStore, safe_path
from pocket_editor_plugins._team_chat_ui import ChatPane

TOKEN = 'test-host-admin-key-only-1234567890'


class ServerClientTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='pocket-project-sync-')
        self.base = Path(self.temp.name)
        self.source = self.base / 'server-project'
        self.source.mkdir()
        (self.source / 'main.py').write_text('print("hello")\n')
        (self.source / 'assets').mkdir()
        (self.source / 'assets/video.mp4').write_bytes(b'video bytes')
        (self.source / '.env').write_text('PRIVATE=value')
        (self.source / '.git').mkdir()
        (self.source / '.git/config').write_text('private git config')
        self.storage = self.base / 'host-data'
        self.server = ChatServer(self.storage, TOKEN, port=0, project=self.source)
        self.address = f'http://127.0.0.1:{self.server.port}'
        self.admin = ChatClient(self.address, TOKEN, 'project', 'Host')
        self.invite_a = self.admin.json_request('/clients', data=json.dumps({'name': 'Alice'}).encode())
        self.invite_b = self.admin.json_request('/clients', data=json.dumps({'name': 'Bob'}).encode())
        self.alice = ChatClient(self.address, self.invite_a['key'], 'project', 'Impersonated name')
        self.bob = ChatClient(self.address, self.invite_b['key'], 'project', 'Bob')
        self.a = self.base / 'alice-copy'; self.a.mkdir()
        self.b = self.base / 'bob-copy'; self.b.mkdir()
        self.copy_a = ProjectCopy(self.alice, self.a, self.base / 'alice-state')
        self.copy_b = ProjectCopy(self.bob, self.b, self.base / 'bob-state')

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def test_initial_copies_and_updates_reach_all_clients(self):
        self.assertEqual(len(self.copy_a.pull()['copied']), 2)
        self.copy_b.pull()
        self.assertEqual((self.a / 'assets/video.mp4').read_bytes(), b'video bytes')
        self.assertFalse((self.a / '.env').exists())
        self.assertFalse((self.a / '.git').exists())
        (self.a / 'main.py').write_text('print("Alice edit")\n')
        (self.a / 'new.txt').write_text('new shared file')
        result = self.copy_a.publish()
        self.assertEqual(set(result['sent']), {'main.py', 'new.txt'})
        self.assertEqual((self.source / 'main.py').read_text(), 'print("Alice edit")\n')
        self.copy_b.pull()
        self.assertEqual((self.b / 'new.txt').read_text(), 'new shared file')
        self.assertEqual((self.b / 'main.py').read_text(), (self.a / 'main.py').read_text())
        backups = list((self.storage / 'project-versions').iterdir())
        self.assertTrue(any(path.read_text() == 'print("hello")\n' for path in backups))

    def test_concurrent_edits_never_overwrite_newer_work(self):
        self.copy_a.pull(); self.copy_b.pull()
        (self.a / 'main.py').write_text('Alice changes')
        (self.b / 'main.py').write_text('Bob changes')
        self.copy_a.publish()
        self.assertEqual(self.copy_b.publish()['conflicts'], ['main.py'])
        self.assertEqual(self.copy_b.pull()['conflicts'], ['main.py'])
        self.assertEqual((self.b / 'main.py').read_text(), 'Bob changes')
        self.assertEqual((self.source / 'main.py').read_text(), 'Alice changes')

    def test_existing_local_files_and_unsent_edits_preserved(self):
        (self.a / 'main.py').write_text('keep this local copy')
        result = self.copy_a.pull()
        self.assertEqual(result['conflicts'], ['main.py'])
        self.assertEqual((self.a / 'main.py').read_text(), 'keep this local copy')
        self.copy_b.pull()
        (self.b / 'main.py').write_text('unsent work')
        self.assertEqual(self.copy_b.pull()['conflicts'], [])
        self.assertEqual((self.b / 'main.py').read_text(), 'unsent work')

    def test_keys_hashes_identity_revocation_and_admin_permissions(self):
        self.assertNotEqual(self.invite_a['key'], self.invite_b['key'])
        self.assertNotIn(self.invite_a['key'].encode(), (self.storage / 'chat.sqlite3').read_bytes())
        self.alice.send('Hello')
        self.assertEqual(self.bob.messages()[0]['author'], 'Alice')
        with self.assertRaisesRegex(RuntimeError, 'Only the host'):
            self.alice.json_request('/clients')
        self.admin.json_request('/clients', data=json.dumps({'revoke': self.invite_a['id']}).encode())
        with self.assertRaisesRegex(RuntimeError, 'revoked'):
            self.alice.messages()
        with self.assertRaises(RuntimeError):
            self.copy_a.pull()
        self.assertEqual(len(self.bob.messages()), 1)
        other_room = ChatClient(self.address, self.invite_b['key'], 'elsewhere', 'Bob')
        with self.assertRaises(RuntimeError):
            other_room.messages()

    def test_server_revision_recheck_catches_stale_upload(self):
        old = self.admin.json_request('/project/manifest')['files']
        item = next(item for item in old if item['path'] == 'main.py')
        (self.source / 'main.py').write_text('host changed the file')
        with self.assertRaisesRegex(RuntimeError, 'Conflict'):
            self.alice.json_request('/project/file', {'path': 'main.py', 'base': item['sha256']}, data=b'stale')
        self.assertEqual((self.source / 'main.py').read_text(), 'host changed the file')

    def test_open_host_file_is_protected_from_client_upload(self):
        store = ProjectStore(self.source, self.storage, guard=lambda name: name != 'main.py')
        before = (self.source / 'main.py').read_bytes()
        with self.assertRaisesRegex(ValueError, 'host has this file open'):
            store.write('main.py', hashlib.sha256(before).hexdigest(), b'client edit')
        self.assertEqual((self.source / 'main.py').read_bytes(), before)

    def test_project_traversal_and_symlinks_rejected(self):
        (self.source / 'outside-link').symlink_to(self.base)
        files = self.admin.json_request('/project/manifest')['files']
        self.assertFalse(any(item['path'].startswith('outside-link') for item in files))
        for name in ('../escape', '/tmp/escape', '.env', 'outside-link/escape', '.git/config', 'C:/escape'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                self.alice.json_request('/project/file', {'path': name, 'base': '-'}, data=b'x')
        self.assertFalse((self.base / 'escape').exists())

    def test_copy_baseline_survives_restart_and_host_deletion_retains_local(self):
        self.copy_a.pull()
        restored = ProjectCopy(self.alice, self.a, self.base / 'alice-state')
        (self.a / 'main.py').write_text('updated after restart')
        self.assertIn('main.py', restored.publish()['sent'])
        (self.source / 'main.py').unlink()
        restored.pull()
        self.assertEqual((self.a / 'main.py').read_text(), 'updated after restart')


class HostClientGuiTests(unittest.TestCase):
    def test_host_generates_key_client_joins_copies_and_sends_changes(self):
        gc.collect()
        with tempfile.TemporaryDirectory(prefix='pocket-host-client-gui-') as temp:
            base = Path(temp)
            shared = base / 'shared'; shared.mkdir()
            (shared / 'hello.txt').write_text('server original')
            local = base / 'client'; local.mkdir()
            root = tk.Tk()
            errors = []
            root.report_callback_exception = lambda *args: errors.append(str(args[1]))
            host = ChatPane(root, root, base / 'host-state')
            guest = ChatPane(root, root, base / 'guest-state')
            def until(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    root.update()
                    if predicate():
                        return
                    time.sleep(.01)
                self.fail(host.status.get() + ' / ' + guest.status.get())
            try:
                host.project.set(str(shared)); host.port.set('0')
                host.connect(True)
                until(lambda: host.connected)
                host.manage_keys(); host.client_name.set('Teammate One'); host.invite()
                until(lambda: bool(host.generated_key.get()))
                guest.address.set(host.address.get()); guest.token.set(host.generated_key.get())
                guest.project.set(str(local)); guest.connect()
                until(lambda: guest.connected)
                self.assertEqual((local / 'hello.txt').read_text(), 'server original')
                (local / 'hello.txt').write_text('client edit')
                guest.sync_project(True)
                until(lambda: not guest.transferring)
                self.assertEqual((shared / 'hello.txt').read_text(), 'client edit')
                self.assertEqual(errors, [])
            finally:
                host.cleanup(); guest.cleanup()
                host.worker.thread.join(timeout=3); guest.worker.thread.join(timeout=3)
                root.destroy()
