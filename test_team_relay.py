"""OS setup, verified downloads, owned relay lifecycle, and invitation integration."""
import hashlib
import io
import json
from pathlib import Path
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pocket_editor_plugins import _team_relay as relay
import test_team_space_ui as fixtures


class RelaySetupTests(unittest.TestCase):
    def test_os_and_architecture_select_correct_asset(self):
        for system, machine, expected in (('Linux','x86_64','cloudflared-linux-amd64'),
                ('Linux','aarch64','cloudflared-linux-arm64'), ('Windows','AMD64','cloudflared-windows-amd64.exe'),
                ('Darwin','arm64','cloudflared-darwin-arm64.tgz')):
            self.assertEqual(relay.platform_info(system,machine)['asset'], expected)
        with self.assertRaises(ValueError):
            relay.platform_info('Unknown','unknown')

    def test_generated_scripts_quote_paths_and_do_not_embed_keys(self):
        script = Path('/tmp/project with spaces/tools/team_relay.py')
        install, start = relay.setup_commands(script,8765,'Linux', '/usr/bin/python3')
        self.assertIn("'/tmp/project with spaces/tools/team_relay.py'", install)
        self.assertTrue(start.endswith('start --port 8765'))
        ps = relay.setup_script(script,8765,'Windows')
        self.assertIn('$LASTEXITCODE',ps)
        self.assertIn('& ',ps)
        self.assertNotIn('Bearer',ps)
        for port in (0,-1,65536):
            with self.assertRaises(ValueError):
                relay.setup_commands(script,port)

    def install(self, base, content=b'official test executable', checksum=None):
        asset='cloudflared-linux-amd64'
        metadata={'assets':[{'name':asset,'browser_download_url':relay.DOWNLOAD_PREFIX+'2026.test/'+asset,
                            'digest':'sha256:'+(checksum or hashlib.sha256(content).hexdigest())}]}
        responses=[io.BytesIO(json.dumps(metadata).encode()),io.BytesIO(content)]
        with patch.object(relay,'installed_cloudflared',return_value=None), patch.object(relay,'platform_info',return_value={'asset':asset,'display':'Linux (amd64)','system':'Linux'}), patch.object(relay,'tools_folder',return_value=base), patch.object(relay,'urlopen',side_effect=responses):
            return relay.install_cloudflared()

    def test_download_checksum_is_verified_before_installing(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            path=self.install(base)
            self.assertEqual(Path(path).read_bytes(),b'official test executable')
            with self.assertRaisesRegex(ValueError,'checksum'):
                self.install(base,b'bad transfer','0'*64)
            self.assertEqual(Path(path).read_bytes(),b'official test executable')

    def test_quick_tunnel_does_not_inherit_host_keys_or_named_tunnel_tokens(self):
        with patch.dict(relay.os.environ, {'TUNNEL_TOKEN':'named-tunnel-token', 'POCKET_TEAM_TOKEN':'host-token',
                                           'CUSTOM_HOST_KEY':'same-secret', 'KEEP_SETTING':'keep'}):
            environment=relay.quick_tunnel_environment(('same-secret','project','Host'))
        self.assertNotIn('TUNNEL_TOKEN',environment)
        self.assertNotIn('POCKET_TEAM_TOKEN',environment)
        self.assertNotIn('CUSTOM_HOST_KEY',environment)
        self.assertEqual(environment['KEEP_SETTING'],'keep')

    def test_relay_url_parser_rejects_lookalike_domains(self):
        self.assertEqual(relay.RELAY_URL.search('INF https://one-two-three.trycloudflare.com |').group(), 'https://one-two-three.trycloudflare.com')
        self.assertIsNone(relay.RELAY_URL.search('https://one-two.trycloudflare.com.evil.example'))
        self.assertIsNone(relay.RELAY_URL.search('http://one-two.trycloudflare.com'))

    def test_managed_process_stops_without_affecting_other_processes(self):
        if sys.platform=='win32':
            self.skipTest('Executable fake is POSIX; Windows command generation is tested separately.')
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            executable=base/'fake-cloudflared'
            executable.write_text('#!'+sys.executable+'\nimport time\nprint("https://unit-test-relay.trycloudflare.com",flush=True)\nprint("Registered tunnel connection",flush=True)\nwhile True: time.sleep(.1)\n')
            executable.chmod(0o755)
            listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
            events=queue.Queue()
            manager=relay.RelayManager(lambda kind,value:events.put(value))
            try:
                with patch.object(relay,'install_cloudflared',return_value=str(executable)), patch.object(relay,'tools_folder',return_value=base):
                    manager.start(listener.getsockname()[1])
                    end=time.monotonic()+8
                    ready=False
                    while time.monotonic()<end:
                        state,detail=events.get(timeout=5)
                        if state=='error': self.fail(detail)
                        if state=='ready':
                            ready=True; break
                    self.assertTrue(ready)
                    manager.stop(); manager.thread.join(timeout=5)
                    self.assertFalse(manager.running)
                    self.assertIsNone(manager.process)
            finally:
                manager.stop(); listener.close()
                if manager.thread: manager.thread.join(timeout=5)


class RelayGuiTests(fixtures.TeamSpaceTests):
    test_team_plugin_has_no_git_panel_and_compact_controls_fit=None
    test_bubbles_align_by_sender_timestamps_and_attachment_button=None

    def pump(self):
        self.root.update()
        time.sleep(.05)
        self.root.update()

    def test_main_host_buttons_run_setup_and_sharing_without_dialog(self):
        self.chat.select_role('Host'); self.pump()
        self.assertTrue(self.chat.install_relay_button.winfo_ismapped())
        self.assertEqual(str(self.chat.start_sharing_button.cget('state')), 'disabled')
        with patch.object(self.chat.relay, 'start') as start:
            self.chat.install_relay_button.invoke()
            start.assert_called_once_with(8765, None, True)
        self.chat.connected = self.chat.hosting = self.chat.admin = True
        self.chat.session_info = {'listen_ip':'127.0.0.1', 'listen_port':8765}
        self.chat.token.set('test-host-key')
        self.chat.show_settings(False)
        self.chat.update_controls(); self.pump()
        self.assertTrue(self.chat.start_sharing_button.winfo_ismapped())
        self.assertIsNone(self.chat.relay_window)
        with patch.object(self.chat.relay, 'start') as start:
            self.chat.start_sharing_button.invoke()
            start.assert_called_once_with(8765, ('test-host-key', self.chat.room.get(), self.chat.author.get()), False)
        self.chat.connected = self.chat.hosting = False
        self.chat.select_role('Client'); self.pump()
        self.assertFalse(self.chat.main_relay_bar.winfo_ismapped())

    def test_detected_os_dialog_and_setup_script_export(self):
        self.chat.select_role('Host')
        self.chat.show_relay(); self.pump()
        self.assertTrue(self.chat.relay_window.winfo_exists())
        with tempfile.TemporaryDirectory() as temp:
            script=Path(temp)/'setup.sh'
            with patch('pocket_editor_plugins._team_chat_ui.filedialog.asksaveasfilename',return_value=str(script)):
                self.chat.save_relay_script()
            self.assertIn('team_relay.py',script.read_text())
            self.assertIn('start --port 8765',script.read_text())

    def test_verified_relay_address_is_used_in_invitations_and_cleared_on_stop(self):
        self.chat.connected=self.chat.hosting=self.chat.admin=True
        self.chat.select_role('Host')
        self.chat.session_info={'listen_ip':'127.0.0.1','listen_port':8765}
        self.chat.local_ip='192.168.1.20'
        self.chat.worker.emit('relay',('ready','https://unit-test.trycloudflare.com'))
        self.pump()
        end=time.monotonic()+2
        while not self.chat.relay_active and time.monotonic()<end: self.pump()
        self.assertTrue(self.chat.relay_active)
        self.chat.manage_keys(); self.pump()
        self.assertEqual(self.chat.invite_address.get(),'https://unit-test.trycloudflare.com')
        self.chat.worker.emit('relay',('stopped',''))
        end=time.monotonic()+2
        while self.chat.relay_active and time.monotonic()<end:self.pump()
        self.assertFalse(self.chat.relay_active)
        self.assertNotIn('trycloudflare',self.chat.invite_address.get())
