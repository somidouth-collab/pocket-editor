"""Portable client invitation validation and real host-export/client-load flow."""
import gc
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from pocket_editor_plugins._team_invites import read_invitation, write_invitation, validate_invitation
from pocket_editor_plugins._team_chat_ui import ChatPane


def sample():
    return {'format': 'pocket-team-invite', 'version': 1, 'server': 'http://127.0.0.1:8765',
            'room': 'Team project', 'client_name': 'Alice', 'client_key': 'client-key-at-least-16-characters',
            'project_name': 'My project'}


class InvitationTests(unittest.TestCase):
    def test_round_trip_private_permissions_and_no_local_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'alice.team-invite'
            value = sample()
            value['project_folder'] = '/host/private/path'
            value['admin_key'] = 'do not export this'
            write_invitation(path, value)
            with patch('pocket_editor_plugins._team_chat.ChatClient.request', side_effect=AssertionError('must remain offline')):
                loaded = read_invitation(path)
            self.assertEqual(loaded, sample())
            self.assertNotIn('/host/private/path', path.read_text())
            self.assertNotIn('admin_key', path.read_text())
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_invalid_files_and_unsupported_versions(self):
        for changes in ({'version': 2}, {'version': True}, {'format': 'other'}, {'client_key': 'short'},
                        {'server': 'file:///etc/passwd'}, {'server': 'http://name:pass@localhost'},
                        {'room': ''}, {'client_name': None}, {'server': 'http://8.8.8.8'},
                        {'server': 'http://0.0.0.0:8765'}, {'server': 'http://127.0.0.1:invalid'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_invitation(dict(sample(), **changes))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.team-invite'
            for raw in ('not JSON', 'x' * 17000, '[]'):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    read_invitation(path)

    def test_invalid_export_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'existing.team-invite'
            path.write_text('keep this')
            with self.assertRaises(ValueError):
                write_invitation(path, {'format': 'invalid'})
            self.assertEqual(path.read_text(), 'keep this')


class InvitationGuiTests(unittest.TestCase):
    def test_host_exports_client_loads_and_joins_with_project_copy(self):
        gc.collect()
        with tempfile.TemporaryDirectory(prefix='pocket-invite-gui-') as temp:
            base = Path(temp)
            shared = base / 'shared'; shared.mkdir()
            local = base / 'local'; local.mkdir()
            (shared / 'hello.txt').write_text('from invitation host')
            root = tk.Tk()
            errors = []
            root.report_callback_exception = lambda *args: errors.append(str(args[1]))
            host = ChatPane(root, root, base / 'host-state')
            guest = ChatPane(root, root, base / 'guest-state')
            host.pack(fill='both', expand=True)
            def until(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    root.update()
                    if predicate():
                        return
                    time.sleep(.01)
                self.fail(host.status.get() + ' / ' + guest.status.get())
            try:
                host.project.set(str(shared)); host.port.set('0'); host.connect(True)
                until(lambda: host.connected)
                host.manage_keys(); host.client_name.set('Preferred client'); host.invite()
                until(lambda: host.last_invite is not None)
                host.invite_address.set(host.address.get())
                invitation = base / 'preferred.team-invite'
                with patch('pocket_editor_plugins._team_chat_ui.filedialog.asksaveasfilename', return_value=str(invitation)):
                    host.export_invitation()
                self.assertTrue(invitation.exists())
                self.assertNotIn(host.token.get(), invitation.read_text())
                with patch('pocket_editor_plugins._team_chat_ui.filedialog.askopenfilename', return_value=str(invitation)), patch('pocket_editor_plugins._team_chat_ui.filedialog.askdirectory', return_value=str(local)):
                    guest.load_invitation()
                self.assertFalse(guest.connected)
                self.assertEqual(guest.author.get(), 'Preferred client')
                self.assertEqual(guest.address.get(), host.address.get())
                self.assertEqual(guest.project.get(), str(local))
                guest.connect()
                until(lambda: guest.connected)
                self.assertEqual((local / 'hello.txt').read_text(), 'from invitation host')
                self.assertFalse(guest.settings_visible)
                guest.composer.insert('1.0', 'Joined with my invitation')
                guest.send()
                until(lambda: 'Joined with my invitation' in host.history.get('1.0', 'end'))
                card = host.history.cards[-1]['message']
                self.assertEqual(card['author'], 'Preferred client')
                self.assertEqual(errors, [])
            finally:
                host.cleanup(); guest.cleanup()
                host.worker.thread.join(timeout=3); guest.worker.thread.join(timeout=3)
                root.destroy()

    def test_cancel_import_keeps_existing_config(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            invitation = base / 'alice.team-invite'
            write_invitation(invitation, sample())
            root = tk.Tk()
            pane = ChatPane(root, root, base / 'state')
            try:
                original = pane.address.get(), pane.room.get(), pane.author.get(), pane.token.get(), pane.project.get()
                with patch('pocket_editor_plugins._team_chat_ui.filedialog.askopenfilename', return_value=str(invitation)), patch('pocket_editor_plugins._team_chat_ui.filedialog.askdirectory', return_value=''):
                    pane.load_invitation()
                self.assertEqual((pane.address.get(), pane.room.get(), pane.author.get(), pane.token.get(), pane.project.get()), original)
            finally:
                pane.cleanup(); pane.worker.thread.join(timeout=3); root.destroy()
