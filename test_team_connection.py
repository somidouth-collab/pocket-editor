"""Host/client roles, real connection endpoints, invitation address suggestions."""
import gc
import io
import json
from pathlib import Path
import socket
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from pocket_editor_plugins._team_chat import ChatClient, ChatServer
from pocket_editor_plugins._team_network import suggested_lan_ipv4, lan_host_url
from pocket_editor_plugins._team_chat_ui import ChatPane

TOKEN = 'host-admin-key-connection-tests-123456789'


class ConnectionBackendTests(unittest.TestCase):
    def test_host_reports_own_listener_and_clients_observed_source(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as temp:
            server = ChatServer(temp, TOKEN, port=0)
            try:
                url = f'http://127.0.0.1:{server.port}'
                admin = ChatClient(url, TOKEN, 'project', 'Host')
                invite = admin.json_request('/clients', data=json.dumps({'name': 'Alice'}).encode())
                client = ChatClient(url, invite['key'], 'project', 'Alice')
                session = client.json_request('/session')
                self.assertEqual(session['listen_ip'], '127.0.0.1')
                self.assertEqual(session['listen_port'], server.port)
                self.assertEqual(session['peer_ip'], '127.0.0.1')
                self.assertGreater(session['peer_port'], 0)
                row = admin.json_request('/clients')['clients'][0]
                self.assertEqual(row['peer_port'], session['peer_port'])
                self.assertEqual(row['peer_ip'], '127.0.0.1')
                self.assertGreater(row['last_seen'], 0)
                with self.assertRaisesRegex(RuntimeError, 'Only the host'):
                    client.json_request('/clients')
            finally:
                server.close()

    def test_lan_suggestion_uses_local_route_without_sending_datagrams(self):
        probe = Mock()
        probe.getsockname.return_value = ('192.168.1.20', 42000)
        factory = Mock()
        factory.return_value.__enter__ = Mock(return_value=probe)
        factory.return_value.__exit__ = Mock(return_value=False)
        with patch('pocket_editor_plugins._team_network.socket.socket', factory):
            self.assertEqual(suggested_lan_ipv4(), '192.168.1.20')
        probe.connect.assert_called_once_with(('192.0.2.1', 9))
        probe.send.assert_not_called()
        probe.sendto.assert_not_called()
        self.assertEqual(lan_host_url('0.0.0.0', 8765, '192.168.1.20'), 'http://192.168.1.20:8765')
        self.assertEqual(lan_host_url('127.0.0.1', 8765, '192.168.1.20'), 'http://127.0.0.1:8765')
        self.assertIsNone(lan_host_url('0.0.0.0', 8765, None))
        self.assertIsNone(lan_host_url('8.8.8.8', 8765))

    def test_private_vpn_shared_address_is_allowed_without_public_http(self):
        client = ChatClient('http://100.80.0.5:8765', TOKEN, 'project', 'Alice')
        self.assertEqual(client.address, 'http://100.80.0.5:8765')
        with self.assertRaises(ValueError):
            ChatClient('http://8.8.8.8:8765', TOKEN, 'project', 'Alice')


class RoleGuiTests(unittest.TestCase):
    def test_client_details_show_host_and_automatic_source_port(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as temp:
            server = ChatServer(Path(temp) / 'host', TOKEN, port=0)
            root = tk.Tk()
            pane = ChatPane(root, root, Path(temp) / 'client')
            try:
                pane.address.set(f'http://127.0.0.1:{server.port}')
                pane.token.set(TOKEN)
                pane.connect()
                end = time.monotonic() + 8
                while not pane.connected and time.monotonic() < end:
                    root.update(); time.sleep(.01)
                self.assertTrue(pane.connected)
                pane.show_network(); root.update()
                details = pane.network_text.get('1.0', 'end')
                self.assertIn('Role: Client', details)
                self.assertIn('Host connection port: ' + str(server.port), details)
                self.assertIn('as seen by host: 127.0.0.1:', details)
                self.assertIn('source port is chosen automatically', details)
                self.assertFalse(pane.hosting)
            finally:
                pane.cleanup(); pane.worker.thread.join(timeout=3); root.destroy(); server.close()

    def test_host_and_client_forms_and_connection_details(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as temp:
            root = tk.Tk()
            errors = []
            root.report_callback_exception = lambda *args: errors.append(str(args[1]))
            pane = ChatPane(root, root, temp)
            pane.pack(fill='both', expand=True)
            def until(predicate):
                end = time.monotonic() + 8
                while time.monotonic() < end:
                    root.update()
                    if predicate():
                        return
                    time.sleep(.01)
                self.fail(pane.status.get())
            try:
                root.update()
                self.assertEqual(pane.role.get(), 'Client')
                self.assertFalse(pane.host_options.winfo_ismapped())
                self.assertTrue(pane.server_entry.winfo_ismapped())
                pane.token.set('old-client-key')
                pane.select_role('Host'); root.update()
                self.assertEqual(pane.token.get(), '')
                self.assertTrue(pane.host_options.winfo_ismapped())
                self.assertFalse(pane.server_entry.winfo_ismapped())
                pane.port.set('0')
                with patch('pocket_editor_plugins._team_chat_ui.suggested_lan_ipv4', return_value='192.168.1.20'):
                    pane.connect(True)
                    until(lambda: pane.connected)
                self.assertGreater(int(pane.port.get()), 0)
                self.assertEqual(pane.suggested_address, f'http://192.168.1.20:{pane.port.get()}')
                pane.show_network(); root.update()
                self.assertIn('Host listening: 0.0.0.0:', pane.network_text.get('1.0', 'end'))
                pane.manage_keys(); root.update()
                self.assertEqual(pane.invite_address.get(), pane.suggested_address)
                pane.leave(); until(lambda: not pane.connecting)
                pane.select_role('Client'); root.update()
                self.assertFalse(pane.host_options.winfo_ismapped())
                self.assertEqual(errors, [])
            finally:
                pane.cleanup(); pane.worker.thread.join(timeout=3); root.destroy()
