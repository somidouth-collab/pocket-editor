"""Host/client project copies, individual keys, and concurrent edit conflicts."""
import gc
import hashlib
import json
import io
import sqlite3
from unittest.mock import patch
from contextlib import closing
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

    def test_manifest_diff_sends_only_changed_paths_and_removals(self):
        initial = self.admin.json_request('/project/manifest')
        known = {item['path']: {'sha256': item['sha256'], 'version': item['version']} for item in initial['files']}
        payload = json.dumps({'project_id': initial['project_id'], 'files': known}).encode()
        unchanged = self.alice.json_request('/project/manifest', data=payload)
        self.assertFalse(unchanged['full']);self.assertEqual(unchanged['files'], [])
        self.assertEqual(unchanged['deleted'], [])
        (self.source / 'main.py').write_text('new host edit')
        (self.source / 'assets/video.mp4').unlink()
        diff = self.alice.json_request('/project/manifest', data=payload)
        self.assertEqual([item['path'] for item in diff['files']], ['main.py'])
        self.assertEqual(diff['files'][0]['version'], 2)
        self.assertEqual(diff['deleted'], [{'path': 'assets/video.mp4', 'version': 2}])
        self.copy_a.pull()
        with patch.object(self.alice, 'request', wraps=self.alice.request) as request:
            self.assertEqual(self.copy_a.pull()['copied'], [])
            self.assertEqual(len(request.call_args_list), 1)
            self.assertEqual(request.call_args_list[0].args[0], '/project/manifest')
            self.assertIn('data', request.call_args_list[0].kwargs)

    def test_version_rejects_aba_edits_even_when_content_hash_matches(self):
        self.copy_a.pull()
        original = (self.source / 'main.py').read_bytes()
        revision = self.copy_a.versions['main.py']
        (self.source / 'main.py').write_bytes(b'intermediate edit')
        self.admin.json_request('/project/manifest')
        (self.source / 'main.py').write_bytes(original)
        self.admin.json_request('/project/manifest')
        with self.assertRaisesRegex(RuntimeError, 'Conflict'):
            self.alice.json_request('/project/file', {'path': 'main.py', 'base': hashlib.sha256(original).hexdigest(),
                                                    'version': revision}, data=b'stale edit')
        (self.a / 'main.py').write_bytes(b'unsent edit')
        result = self.copy_a.publish()
        self.assertEqual(result['conflicts'], ['main.py'])
        recovery = Path(result['conflict_copies']['main.py'])
        self.assertEqual(recovery.read_bytes(), b'unsent edit')
        self.assertEqual(json.loads(recovery.with_suffix('.json').read_text())['path'], 'main.py')
        self.assertEqual((self.source / 'main.py').read_bytes(), original)
        self.assertEqual((self.a / 'main.py').read_bytes(), b'unsent edit')

    def test_persistent_index_deduplicates_content_and_audits_author(self):
        self.copy_a.pull()
        (self.a / 'main.py').write_bytes(b'same content')
        (self.a / 'duplicate.txt').write_bytes(b'same content')
        self.copy_a.publish()
        store = ProjectStore(self.source, self.storage)
        entries = {item['path']: item for item in store.manifest()['files']}
        self.assertEqual(entries['main.py']['version'], 2)
        self.assertEqual(entries['main.py']['sha256'], entries['duplicate.txt']['sha256'])
        self.assertEqual((store.backups / hashlib.sha256(b'same content').hexdigest()).read_bytes(), b'same content')
        history = self.admin.json_request('/project/history', {'path': 'main.py'})['history']
        self.assertEqual(history[0]['author'], 'Alice')
        self.assertEqual(history[0]['action'], 'update')
        with self.assertRaisesRegex(RuntimeError, 'Only the host'):
            self.alice.json_request('/project/history', {'path': 'main.py'})
        (self.source / 'main.py').unlink();store.manifest()
        self.assertEqual(store.history('main.py')[0]['action'], 'delete')
        (self.source / 'main.py').write_text('recreated')
        restarted = ProjectStore(self.source, self.storage)
        recreated = next(item for item in restarted.manifest()['files'] if item['path'] == 'main.py')
        self.assertEqual(recreated['version'], 4)
        with closing(sqlite3.connect(store.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM project_history WHERE path=?', ('main.py',)).fetchone()[0], 4)

    def test_client_rejects_malformed_manifest_before_writing_any_file(self):
        good = self.admin.json_request('/project/manifest')
        malformed = [None, [], {'available': True, 'files': [None]},
                     dict(good, files=[{'path': '../outside', 'sha256': 'a'*64, 'size': 1, 'version': 1}]),
                     dict(good, files=[dict(good['files'][0], version=True)]),
                     dict(good, files=[dict(good['files'][0], size=51*1024*1024)]),
                     dict(good, deleted=[{'path': '../outside', 'version': 1}]),
                     dict(good, full=False), dict(good, protocol=True)]
        (self.a / 'link').symlink_to(self.base, target_is_directory=True)
        malformed.append(dict(good, files=[dict(good['files'][0], path='link/escape')]))
        for response in malformed:
            with self.subTest(response=type(response).__name__), patch.object(self.alice, 'json_request', return_value=response):
                with self.assertRaises(ValueError):self.copy_a.pull()
                self.assertIsNone(self.copy_a.project_id)
                self.assertFalse((self.a / 'main.py').exists())
        self.assertFalse((self.base / 'escape').exists())

    def test_corrupt_download_keeps_existing_copy_and_baseline(self):
        self.copy_a.pull()
        before = (self.a / 'main.py').read_bytes()
        baseline = dict(self.copy_a.base)
        (self.source / 'main.py').write_bytes(b'new remote data')
        original = self.alice.request
        def request(endpoint, *args, **kwargs):
            if endpoint == '/project/file':return io.BytesIO(b'bad data')
            return original(endpoint, *args, **kwargs)
        with patch.object(self.alice, 'request', side_effect=request):
            with self.assertRaisesRegex(ValueError, 'checksum'):self.copy_a.pull()
        self.assertEqual((self.a / 'main.py').read_bytes(), before)
        self.assertEqual(self.copy_a.base, baseline)

    def test_host_can_remove_client_and_key_stays_invalid_after_restart(self):
        with self.assertRaisesRegex(RuntimeError, 'Only the host'):
            self.bob.json_request('/clients', data=json.dumps({'delete': self.invite_a['id']}).encode())
        self.assertEqual(self.admin.json_request('/clients', data=json.dumps({'delete': self.invite_a['id']}).encode()), {'deleted': True})
        self.assertNotIn(self.invite_a['id'], {row['id'] for row in self.admin.json_request('/clients')['clients']})
        for endpoint in ('/session', '/messages', '/project/manifest'):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(RuntimeError, 'revoked'):
                self.alice.json_request(endpoint)
        new = self.admin.json_request('/clients', data=json.dumps({'name': 'Replacement'}).encode())
        self.assertGreater(new['id'], self.invite_b['id'])
        self.server.close()
        self.server = ChatServer(self.storage, TOKEN, port=0, project=self.source)
        address = f'http://127.0.0.1:{self.server.port}'
        removed = ChatClient(address, self.invite_a['key'], 'project', 'Alice')
        with self.assertRaisesRegex(RuntimeError, 'revoked'):removed.messages()
        remaining = ChatClient(address, self.invite_b['key'], 'project', 'Bob')
        self.assertEqual(remaining.messages(), [])

    def test_scoped_readonly_and_admin_invitations(self):
        invite = self.admin.json_request('/clients', data=json.dumps(
            {'name': 'Viewer', 'role': 'read-only', 'folders': ['assets']}).encode())
        viewer = ChatClient(self.address, invite['key'], 'project', 'Viewer')
        manifest = viewer.json_request('/project/manifest')
        self.assertEqual([item['path'] for item in manifest['files']], ['assets/video.mp4'])
        self.assertEqual(viewer.json_request('/session')['role'], 'read-only')
        with self.assertRaisesRegex(RuntimeError, 'cannot access'):
            viewer.json_request('/project/file', {'path': 'main.py', 'sha256': 'a'*64})
        with self.assertRaisesRegex(RuntimeError, 'read-only'):
            viewer.json_request('/project/file', {'path': 'assets/new.txt', 'base': '-', 'version': 0}, data=b'no')
        viewer.send('Can you review this?')
        self.assertEqual(self.bob.messages()[-1]['author'], 'Viewer')
        with self.assertRaisesRegex(RuntimeError, 'Only the host'):
            viewer.json_request('/clients')
        invite = self.admin.json_request('/clients', data=json.dumps(
            {'name': 'Co-admin', 'role': 'admin'}).encode())
        coadmin = ChatClient(self.address, invite['key'], 'project', 'Co-admin')
        self.assertTrue(coadmin.json_request('/session')['admin'])
        self.assertTrue(coadmin.json_request('/clients')['clients'])
        for invalid in ({'role': 'owner'}, {'role': 'member', 'folders': ['../escape']},
                        {'role': 'admin', 'folders': ['assets']}):
            with self.assertRaises(RuntimeError):
                self.admin.json_request('/clients', data=json.dumps(dict(name='Invalid', **invalid)).encode())

    def test_server_rejects_bad_manifest_and_negative_versions(self):
        for payload in ([], {'files': []}, {'files': {'../escape': {'sha256': 'a'*64, 'version': 1}}}):
            with self.assertRaises(RuntimeError):
                self.alice.json_request('/project/manifest', data=json.dumps(payload).encode())
        initial = next(item for item in self.admin.json_request('/project/manifest')['files'] if item['path'] == 'main.py')
        with self.assertRaises(RuntimeError):
            self.alice.json_request('/project/file', {'path': 'main.py', 'base': initial['sha256'], 'version': -1}, data=b'invalid')


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
                host.enqueue('clients')
                until(lambda: bool(host.client_tree.get_children()))
                row = host.client_tree.get_children()[0]
                host.client_tree.selection_set(row)
                with patch('pocket_editor_plugins._team_chat_ui.messagebox.askyesno', return_value=False):host.delete_client()
                self.assertTrue(host.client_tree.get_children())
                key = host.generated_key.get()
                with patch('pocket_editor_plugins._team_chat_ui.messagebox.askyesno', return_value=True):host.delete_client()
                until(lambda: not host.client_tree.get_children() and 'Client removed' in host.status.get())
                self.assertEqual(host.generated_key.get(), '')
                removed = ChatClient(host.address.get(), key, 'project', 'Removed client')
                with self.assertRaisesRegex(RuntimeError, 'revoked'):removed.messages()
                self.assertTrue((local / 'hello.txt').exists())
                self.assertEqual(errors, [])
            finally:
                host.cleanup(); guest.cleanup()
                host.worker.thread.join(timeout=3); guest.worker.thread.join(timeout=3)
                root.destroy()
