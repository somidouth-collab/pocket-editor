"""User-local cloudflared setup and a managed temporary HTTPS relay."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.request import Request, urlopen

RELEASE_API = 'https://api.github.com/repos/cloudflare/cloudflared/releases/latest'
DOWNLOAD_PREFIX = 'https://github.com/cloudflare/cloudflared/releases/download/'
MAX_DOWNLOAD = 160 * 1024 * 1024
RELAY_URL = re.compile(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?=[/\s|]|$)')


def platform_info(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64',
            'i386': '386', 'i686': '386', 'x86': '386', 'armv7l': 'arm', 'armv6l': 'arm'}.get(machine)
    family = {'Linux': 'linux', 'Darwin': 'darwin', 'Windows': 'windows'}.get(system)
    if not family or not arch:
        raise ValueError(f'Unsupported relay platform: {system} {machine}. Install cloudflared manually.')
    asset = f'cloudflared-{family}-{arch}' + ('.exe' if family == 'windows' else '.tgz' if family == 'darwin' else '')
    return {'system': system, 'arch': arch, 'asset': asset, 'display': f'{system} ({arch})'}


def tools_folder():
    if platform.system() == 'Windows':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'PocketEditor/tools'
    if platform.system() == 'Darwin':
        return Path.home() / 'Library/Application Support/PocketEditor/tools'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'pocket-editor/tools'


def installed_cloudflared():
    cached = tools_folder() / ('cloudflared.exe' if platform.system() == 'Windows' else 'cloudflared')
    return str(cached) if cached.is_file() else shutil.which('cloudflared')


def setup_commands(script, port, system=None, executable=None):
    system = system or platform.system()
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('Start the host first or choose a port from 1 to 65535.')
    arguments = [executable or sys.executable, str(Path(script).absolute())]
    if system == 'Windows':
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = '& ' + ' '.join(quote(item) for item in arguments)
    else:
        command = ' '.join(shlex.quote(str(item)) for item in arguments)
    return command + ' setup', command + f' start --port {port}'


def setup_script(script, port, system=None):
    system = system or platform.system()
    install, start = setup_commands(script, port, system)
    if system == 'Windows':
        return "$ErrorActionPreference = 'Stop'\n" + install + "\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n" + start + '\n'
    return '#!/bin/sh\nset -eu\n' + install + '\n' + start + '\n'


def _request(url):
    return Request(url, headers={'User-Agent': 'PocketEditor-TeamRelay', 'Accept': 'application/vnd.github+json'})


def install_cloudflared(force=False, progress=None, cancelled=None):
    existing = installed_cloudflared()
    if existing and not force:
        return existing
    info = platform_info()
    progress = progress or (lambda _text: None)
    cancelled = cancelled or threading.Event()
    progress('Checking the official Cloudflare release for ' + info['display'])
    with urlopen(_request(RELEASE_API), timeout=20) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Release metadata is too large.')
    release = json.loads(raw)
    asset = next((item for item in release.get('assets', []) if item.get('name') == info['asset']), None)
    if asset is None:
        raise ValueError('No official cloudflared download for ' + info['display'])
    url = asset.get('browser_download_url', '')
    if not url.startswith(DOWNLOAD_PREFIX) or not url.endswith('/' + info['asset']):
        raise ValueError('Unexpected cloudflared download address.')
    digest = asset.get('digest', '') or ''
    expected = digest.removeprefix('sha256:') if digest.startswith('sha256:') else ''
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        match = re.search(r'(?m)^\s*' + re.escape(info['asset']) + r':\s*([0-9a-f]{64})\s*$', release.get('body', ''))
        expected = match.group(1) if match else ''
    if not expected:
        raise ValueError('Official release checksum is unavailable. No executable was installed.')
    progress('Downloading and verifying cloudflared (no administrator privileges required)…')
    data = bytearray()
    with urlopen(_request(url), timeout=30) as response:
        while True:
            if cancelled.is_set():
                raise RuntimeError('Relay setup canceled.')
            chunk = response.read(65536)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_DOWNLOAD:
                raise ValueError('Cloudflared download exceeded the size limit.')
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Cloudflared checksum verification failed. No executable was installed.')
    binary = bytes(data)
    if info['asset'].endswith('.tgz'):
        with tarfile.open(fileobj=io.BytesIO(binary), mode='r:gz') as archive:
            member = next((item for item in archive.getmembers() if item.name.lstrip('./') == 'cloudflared' and item.isfile()), None)
            if member is None or member.size > MAX_DOWNLOAD:
                raise ValueError('The cloudflared archive does not contain a supported executable.')
            with archive.extractfile(member) as stream:
                binary = stream.read(MAX_DOWNLOAD + 1)
            if len(binary) > MAX_DOWNLOAD:
                raise ValueError('Cloudflared executable exceeded the size limit.')
    target = tools_folder() / ('cloudflared.exe' if info['system'] == 'Windows' else 'cloudflared')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.cloudflared-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(binary)
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o755)
        if cancelled.is_set():
            raise RuntimeError('Relay setup canceled.')
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    progress('Cloudflared installed in your user tools folder.')
    return str(target)


def quick_tunnel_environment(credentials=None):
    secret = credentials[0] if credentials else None
    return {key: value for key, value in os.environ.items()
            if not key.startswith(('TUNNEL_', 'POCKET_TEAM_')) and (secret is None or value != secret)}


class RelayManager:
    """Own only the process started by this manager; report events outside Tk."""
    def __init__(self, notify):
        self.notify = notify
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.thread = None
        self.process = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.cancelled.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.terminate()
                except OSError:
                    pass

    def start(self, port, credentials=None, setup_only=False):
        if self.running:
            raise ValueError('Relay setup or sharing is already running.')
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError('Start the host first to obtain its listening port.')
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(port, credentials, setup_only), daemon=True)
        self.thread.start()

    def _event(self, state, detail=''):
        self.notify('relay', (state, detail))

    def _run(self, port, credentials, setup_only):
        process = None
        try:
            executable = install_cloudflared(progress=lambda text: self._event('progress', text), cancelled=self.cancelled)
            if self.cancelled.is_set():
                return
            if setup_only:
                self._event('installed', executable)
                return
            with socket.create_connection(('127.0.0.1', port), timeout=3):
                pass
            # Isolate quick-tunnel settings from any user's existing named tunnels.
            config = tools_folder() / 'pocket-team-quick.yml'
            config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            config.write_text('{}\n', encoding='utf-8')
            command = [executable, 'tunnel', '--config', str(config), '--no-autoupdate',
                       '--url', f'http://127.0.0.1:{port}', '--protocol', 'http2']
            self._event('starting', 'Starting a temporary HTTPS tunnel…')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with self.lock:
                if self.cancelled.is_set():
                    return
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, env=quick_tunnel_environment(credentials), **options)
                self.process = process
            lines = []
            started = time.monotonic()
            url = None
            candidate_url = None
            edge_connected = False
            # A small reader thread avoids blocking cancellation on silent output.
            import queue
            output = queue.Queue(maxsize=128)
            def read_output():
                try:
                    for raw in iter(process.stdout.readline, b''):
                        while not self.cancelled.is_set():
                            try:
                                output.put(raw[:8192], timeout=.1)
                                break
                            except queue.Full:
                                pass
                finally:
                    process.stdout.close()
            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            while not self.cancelled.is_set():
                try:
                    line = output.get(timeout=.2).decode('utf-8', 'replace').strip()
                    lines.append(line)
                    lines = lines[-12:]
                    match = RELAY_URL.search(line)
                    if match and candidate_url is None:
                        candidate_url = match.group()
                        self._event('progress', 'HTTPS address allocated; waiting for the relay connection…')
                    if 'Registered tunnel connection' in line:
                        edge_connected = True
                except queue.Empty:
                    pass
                if candidate_url and edge_connected and url is None:
                    # cloudflared prints its URL before its edge connection is
                    # established. Resolving that name too early can cache NXDOMAIN.
                    self._event('progress', 'Relay connected; checking HTTPS access to this host…')
                    self.cancelled.wait(2)
                    if self.cancelled.is_set():
                        break
                    if credentials:
                        from pocket_editor_plugins._team_chat import ChatClient
                        token, room, author = credentials
                        verified = False
                        last_failure = 'No authenticated response received.'
                        deadline = min(started + 120, time.monotonic() + 90)
                        while time.monotonic() < deadline:
                            if self.cancelled.is_set() or process.poll() is not None:
                                break
                            try:
                                session = ChatClient(candidate_url, token, room, author, timeout=3).json_request('/session')
                                if session.get('admin') and session.get('listen_port') == port:
                                    verified = True
                                    break
                                last_failure = 'The HTTPS response did not identify this host.'
                            except (OSError, RuntimeError, ValueError) as error:
                                last_failure = str(error)
                            self.cancelled.wait(2)
                        if not verified:
                            while True:
                                try:
                                    lines.append(output.get_nowait().decode('utf-8', 'replace').strip())
                                except queue.Empty:
                                    break
                            raise RuntimeError('Tunnel could not be verified: ' + last_failure + '\n' + '\n'.join(lines[-6:]))
                    url = candidate_url
                    self._event('ready', url)
                if process.poll() is not None:
                    raise RuntimeError('Cloudflared exited. ' + '\n'.join(lines[-6:]))
                if url is None and time.monotonic() - started > 120:
                    raise RuntimeError('Tunnel setup timed out. ' + '\n'.join(lines[-6:]))
        except Exception as error:
            if not self.cancelled.is_set():
                self._event('error', str(error))
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=3)
                with self.lock:
                    self.process = None
            if not setup_only:
                self._event('stopped')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Auto-detect OS and set up a temporary HTTPS relay for Pocket Editor.')
    parser.add_argument('command', choices=('check', 'setup', 'start'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--force', action='store_true', help='Reinstall the user-local executable.')
    args = parser.parse_args(argv)
    info = platform_info()
    print('Detected platform:', info['display'])
    if args.command == 'check':
        print('Cloudflared:', installed_cloudflared() or 'Not installed')
        return 0
    if args.command == 'setup':
        print('Cloudflared:', install_cloudflared(force=args.force, progress=print))
        return 0
    finished = threading.Event()
    failed = []
    def notify(_kind, item):
        state, detail = item
        if detail:
            print(state + ': ' + detail, flush=True)
        if state == 'error':
            failed.append(detail)
        if state == 'stopped':
            finished.set()
    manager = RelayManager(notify)
    manager.start(args.port)
    try:
        while not finished.wait(.5):
            pass
    except KeyboardInterrupt:
        manager.stop()
    finally:
        manager.stop()
        if manager.thread:
            manager.thread.join(timeout=5)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
