"""Public IP discovery validation and opt-in invitation UI, without internet calls."""
import io
import json
import socket
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from pocket_editor_plugins._team_network import PUBLIC_IP_URL, PUBLIC_IP_SERVICES, LOOKUP_TIMEOUT, detect_public_ipv4, public_https_url
import test_team_space_ui as fixtures


class NetworkTests(unittest.TestCase):
    def lookup(self, body):
        opener = Mock()
        opener.open.side_effect = lambda *_args, **_kwargs: io.BytesIO(body)
        with patch('pocket_editor_plugins._team_network.build_opener', return_value=opener):
            result = detect_public_ipv4()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, PUBLIC_IP_URL)
        self.assertIsNone(request.data)
        self.assertNotIn('Authorization', request.headers)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], LOOKUP_TIMEOUT)
        return result

    def test_detects_and_validates_public_ipv4_without_credentials(self):
        self.assertEqual(self.lookup(b'{"ip":"8.8.8.8"}'), '8.8.8.8')

    def test_rejects_private_non_ip_ipv6_and_oversized_responses(self):
        for body in (b'{"ip":"192.168.1.1"}', b'{"ip":"127.0.0.1"}', b'{"ip":"100.64.0.1"}',
                     b'{"ip":"224.0.0.1"}', b'{"ip":"::1"}', b'{"ip":"not-an-ip"}', b'[]',
                     b'not JSON', b'x' * 1025):
            with self.subTest(body=body[:50]), self.assertRaises(RuntimeError):
                self.lookup(body)

    def test_unavailable_service_is_actionable(self):
        opener = Mock()
        opener.open.side_effect = URLError('offline')
        with patch('pocket_editor_plugins._team_network.build_opener', return_value=opener), self.assertRaisesRegex(RuntimeError, 'internet access'):
            detect_public_ipv4()

    def test_dns_failure_uses_second_https_provider(self):
        opener = Mock()
        opener.open.side_effect = [URLError(socket.gaierror(-2, 'Name or service not known')), io.BytesIO(b'8.8.8.8\n')]
        with patch('pocket_editor_plugins._team_network.build_opener', return_value=opener):
            self.assertEqual(detect_public_ipv4(), '8.8.8.8')
        self.assertEqual([call.args[0].full_url for call in opener.open.call_args_list], list(PUBLIC_IP_SERVICES[:2]))

    def test_all_provider_dns_failures_are_reported_explicitly(self):
        opener = Mock()
        opener.open.side_effect = URLError(socket.gaierror(-2, 'DNS unavailable'))
        with patch('pocket_editor_plugins._team_network.build_opener', return_value=opener), self.assertRaisesRegex(RuntimeError, 'DNS lookup failed') as error:
            detect_public_ipv4()
        self.assertIn('checkip.amazonaws.com', str(error.exception))
        self.assertEqual(opener.open.call_count, len(PUBLIC_IP_SERVICES))

    def test_invalid_primary_response_uses_fallback(self):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(b'<html>unavailable</html>'), io.BytesIO(b'8.8.8.8')]
        with patch('pocket_editor_plugins._team_network.build_opener', return_value=opener):
            self.assertEqual(detect_public_ipv4(), '8.8.8.8')

    def test_https_url_and_port_validation(self):
        self.assertEqual(public_https_url('8.8.8.8'), 'https://8.8.8.8')
        self.assertEqual(public_https_url('8.8.8.8', '8443'), 'https://8.8.8.8:8443')
        for port in ('0', '-1', '65536', 'invalid'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                public_https_url('8.8.8.8', port)


class PublicIpGuiTests(fixtures.TeamSpaceTests):
    test_team_plugin_has_no_git_panel_and_compact_controls_fit = None
    test_bubbles_align_by_sender_timestamps_and_attachment_button = None

    def until(self, predicate):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for public IP detection')

    def test_top_lookup_works_for_both_roles_without_connection_or_dialogs(self):
        for role in ('Host', 'Client'):
            with self.subTest(role=role), patch('pocket_editor_plugins._team_chat_ui.detect_public_ipv4', return_value='8.8.8.8'):
                self.chat.select_role(role)
                self.root.update()
                self.assertTrue(self.chat.main_detect_ip_button.winfo_ismapped())
                self.assertLess(self.chat.public_ip_bar.winfo_rooty(), self.chat.settings_box.winfo_rooty())
                self.assertIsNone(self.chat.key_window)
                self.assertIsNone(self.chat.network_window)
                self.chat.main_detect_ip_button.invoke()
                self.until(lambda: not self.chat.public_lookup_pending)
                self.assertEqual(self.chat.public_ip_value.get(), '8.8.8.8')
                self.assertFalse(self.chat.connected)
                self.chat.show_settings(False)
                self.root.update()
                self.assertTrue(self.chat.main_detect_ip_button.winfo_ismapped())
                self.chat.show_settings(True)

    def test_top_lookup_error_is_visible_and_retry_succeeds(self):
        with patch('pocket_editor_plugins._team_chat_ui.detect_public_ipv4', side_effect=RuntimeError('DNS lookup failed')):
            self.chat.main_detect_ip_button.invoke()
            self.until(lambda: not self.chat.public_lookup_pending)
        self.assertIn('DNS lookup failed', self.chat.public_lookup_status.get())
        self.assertEqual(str(self.chat.main_detect_ip_button.cget('state')), 'normal')
        with patch('pocket_editor_plugins._team_chat_ui.detect_public_ipv4', return_value='8.8.8.8'):
            self.chat.main_detect_ip_button.invoke()
            self.until(lambda: not self.chat.public_lookup_pending)
        self.assertEqual(self.chat.public_ip_value.get(), '8.8.8.8')

    def test_lookup_is_explicit_and_preserves_url_until_use_clicked(self):
        with patch('pocket_editor_plugins._team_chat_ui.detect_public_ipv4', return_value='8.8.8.8') as detect:
            self.chat.connected = self.chat.admin = True
            self.chat.manage_keys()
            self.root.update()
            detect.assert_not_called()
            self.chat.invite_address.set('https://chat.example.test')
            self.chat.discover_public_ip()
            self.until(lambda: not self.chat.public_ip_pending)
            self.assertEqual(self.chat.public_ip.get(), '8.8.8.8')
            self.assertEqual(self.chat.invite_address.get(), 'https://chat.example.test')
            self.chat.public_https_port.set('8443')
            self.chat.use_public_ip()
            self.assertEqual(self.chat.invite_address.get(), 'https://8.8.8.8:8443')
            detect.assert_called_once()

    def test_lookup_failure_preserves_existing_invitation_address(self):
        self.chat.connected = self.chat.admin = True
        self.chat.manage_keys()
        self.chat.invite_address.set('https://chat.example.test')
        with patch('pocket_editor_plugins._team_chat_ui.detect_public_ipv4', side_effect=RuntimeError('offline')):
            self.chat.discover_public_ip()
            self.until(lambda: not self.chat.public_ip_pending)
        self.assertEqual(self.chat.invite_address.get(), 'https://chat.example.test')
        self.assertIn('offline', self.chat.public_ip_status.get())
