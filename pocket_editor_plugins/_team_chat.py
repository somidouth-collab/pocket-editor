"""Private team chat transport and persistent attachment store (Python stdlib only)."""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from pocket_editor_plugins._team_project_sync import ProjectStore, ConflictError

MAX_FILE = 50 * 1024 * 1024
MAX_JSON = 64 * 1024
MAX_STORAGE = 1024 * 1024 * 1024


def field(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\0' in value:
        raise ValueError(f'Invalid {label} (1–{limit} characters).')
    return value.strip()


def attachment_name(value):
    value = field(value, 'filename', 240)
    if value in ('.', '..') or any(c in value for c in '/\\') or any(ord(c) < 32 for c in value):
        raise ValueError('Attachment must have a plain filename.')
    return value


class ChatStore:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.files = self.folder / 'files'
        self.files.mkdir(exist_ok=True, mode=0o700)
        self.database = self.folder / 'chat.sqlite3'
        self.lock = threading.Lock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS messages ('
                       'id INTEGER PRIMARY KEY, room TEXT, author TEXT, text TEXT, '
                       'created REAL, file_id TEXT, name TEXT, size INTEGER, sha256 TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS room_id ON messages(room, id)')
            db.execute('CREATE TABLE IF NOT EXISTS client_keys (id INTEGER PRIMARY KEY, digest TEXT UNIQUE, name TEXT, room TEXT, revoked INTEGER DEFAULT 0)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def messages(self, room, after):
        field(room, 'room', 100)
        if after < 0:
            raise ValueError('Invalid message cursor.')
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                'SELECT * FROM messages WHERE room=? AND id>? ORDER BY id LIMIT 20', (room, after))]

    def post(self, room, author, text):
        room, author = field(room, 'room', 100), field(author, 'name', 80)
        text = field(text, 'message', 8000)
        with self.lock, self.connect() as db:
            cursor = db.execute('INSERT INTO messages(room,author,text,created) VALUES (?,?,?,?)',
                                (room, author, text, time.time()))
            return cursor.lastrowid

    def upload(self, room, author, name, stream, length):
        room, author = field(room, 'room', 100), field(author, 'name', 80)
        name = attachment_name(name)
        if not 0 <= length <= MAX_FILE:
            raise ValueError('Attachments must be at most 50 MiB.')
        with self.lock:
            # Include orphaned files in the quota (for example after a crash).
            used = sum(path.stat().st_size for path in self.files.iterdir() if path.is_file())
            if used + length > MAX_STORAGE:
                raise ValueError('Host attachment storage is full (1 GiB).')
            file_id = secrets.token_hex(16)
            target = self.files / file_id
            digest = hashlib.sha256()
            try:
                with target.open('xb') as output:
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError('Incomplete attachment upload.')
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                with self.connect() as db:
                    cursor = db.execute('INSERT INTO messages(room,author,text,created,file_id,name,size,sha256) '
                                        'VALUES (?,?,?,?,?,?,?,?)',
                                        (room, author, '', time.time(), file_id, name, length, digest.hexdigest()))
                    return cursor.lastrowid
            except Exception:
                target.unlink(missing_ok=True)
                raise

    def attachment(self, room, file_id):
        field(room, 'room', 100)
        if not re.fullmatch(r'[0-9a-f]{32}', file_id):
            raise ValueError('Invalid attachment ID.')
        with self.connect() as db:
            row = db.execute('SELECT size,sha256 FROM messages WHERE room=? AND file_id=?',
                             (room, file_id)).fetchone()
        if row is None:
            raise FileNotFoundError('Attachment not found in this room.')
        return self.files / file_id, row[0], row[1]


    def invite(self, name, room):
        name, room = field(name, 'client name', 80), field(room, 'room', 100)
        key = secrets.token_urlsafe(32)
        hashed = hashlib.sha256(key.encode()).hexdigest()
        with self.lock, self.connect() as db:
            cursor = db.execute('INSERT INTO client_keys(digest,name,room) VALUES (?,?,?)', (hashed, name, room))
            return {'id': cursor.lastrowid, 'name': name, 'key': key, 'room': room}

    def clients(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT id,name,room,revoked FROM client_keys ORDER BY id')]

    def revoke(self, ident):
        with self.lock, self.connect() as db:
            db.execute('UPDATE client_keys SET revoked=1 WHERE id=?', (int(ident),))

    def identify(self, key):
        hashed = hashlib.sha256(key.encode()).hexdigest()
        with self.connect() as db:
            return db.execute('SELECT name,room,id FROM client_keys WHERE digest=? AND revoked=0', (hashed,)).fetchone()


class ChatServer:
    def __init__(self, folder, token, bind='127.0.0.1', port=8765, project=None, project_room='project', project_guard=None):
        if not isinstance(token, str) or len(token) < 16 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('Use an access token of at least 16 ASCII characters without spaces.')
        store = ChatStore(folder)
        project_store = ProjectStore(project, folder, project_guard) if project else None
        project_room = field(project_room, "room", 100)
        peers = {}
        peers_lock = threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Do not log access tokens, URLs, or chat contents.

            def setup(self):
                super().setup()
                self.connection.settimeout(20)

            def json_reply(self, status, value):
                data = json.dumps(value, ensure_ascii=True).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)

            def dispatch(self, method):
                authorization = self.headers.get('Authorization', '')
                supplied = authorization[len('Bearer '):] if authorization.startswith('Bearer ') else ''
                admin = hmac.compare_digest(supplied.encode(), token.encode())
                identity = None if admin else store.identify(supplied)
                if not admin and identity is None:
                    self.json_reply(401, {'error': 'Incorrect or revoked client join key.'})
                    return
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                get = lambda key: query.get(key, [''])[0]
                room = get('room')
                if identity and identity[1] != room:
                    self.json_reply(403, {'error': 'This join key belongs to another room.'})
                    return
                author = identity[0] if identity else get('author')
                if identity:
                    with peers_lock:
                        peers[identity[2]] = {'peer_ip': self.client_address[0], 'peer_port': self.client_address[1], 'last_seen': time.time()}
                try:
                    if parsed.path == '/session' and method == 'GET':
                        self.json_reply(200, {'admin': admin, 'project': project_store is not None,
                                              'peer_ip': self.client_address[0], 'peer_port': self.client_address[1],
                                              'listen_ip': self.server.server_address[0], 'listen_port': self.server.server_address[1]})
                    elif parsed.path == '/clients':
                        if not admin:
                            self.json_reply(403, {'error': 'Only the host can manage client keys.'})
                            return
                        if method == 'GET':
                            rows = store.clients()
                            with peers_lock:
                                for row in rows:
                                    row.update(peers.get(row['id'], {}))
                            self.json_reply(200, {'clients': rows})
                        else:
                            length = int(self.headers.get('Content-Length', '-1'))
                            if not 0 < length <= MAX_JSON:
                                raise ValueError('Invalid client request.')
                            data = json.loads(self.rfile.read(length))
                            if data.get('revoke') is not None:
                                store.revoke(data['revoke'])
                                self.json_reply(200, {'revoked': True})
                            else:
                                self.json_reply(201, store.invite(data.get('name'), room))
                    elif parsed.path.startswith('/project/'):
                        if room != project_room:
                            self.json_reply(403, {'error': 'This room does not share the host project.'})
                            return
                        if parsed.path == '/project/manifest' and method == 'GET':
                            self.json_reply(200, project_store.manifest() if project_store else {'available': False})
                        elif project_store is None:
                            raise ValueError('The host has not selected a project folder.')
                        elif parsed.path == '/project/file' and method == 'GET':
                            data = project_store.read(get('path'), get('sha256'))
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/octet-stream')
                            self.send_header('Content-Length', str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                        elif parsed.path == '/project/file' and method == 'POST':
                            length = int(self.headers.get('Content-Length', '-1'))
                            if not 0 <= length <= MAX_FILE:
                                raise ValueError('Project files must be at most 50 MiB.')
                            data = self.rfile.read(length)
                            if len(data) != length:
                                raise ValueError('Incomplete project upload.')
                            expected = get('base')
                            if expected != '-' and not re.fullmatch(r'[0-9a-f]{64}', expected):
                                raise ValueError('A project revision is required.')
                            self.json_reply(200, project_store.write(get('path'), None if expected == '-' else expected, data))
                        else:
                            self.json_reply(404, {'error': 'Unknown project endpoint.'})
                    elif method == 'GET' and parsed.path == '/messages':
                        self.json_reply(200, {'messages': store.messages(room, int(get('after') or '0'))})
                    elif method == 'POST' and parsed.path == '/messages':
                        length = int(self.headers.get('Content-Length', '-1'))
                        if not 0 < length <= MAX_JSON:
                            raise ValueError('Invalid message size.')
                        data = json.loads(self.rfile.read(length))
                        if not isinstance(data, dict):
                            raise ValueError('Expected a message object.')
                        ident = store.post(room, identity[0] if identity else data.get('author'), data.get('text'))
                        self.json_reply(201, {'id': ident})
                    elif method == 'POST' and parsed.path == '/files':
                        ident = store.upload(room, author, get('name'), self.rfile,
                                             int(self.headers.get('Content-Length', '-1')))
                        self.json_reply(201, {'id': ident})
                    elif method == 'GET' and parsed.path.startswith('/files/'):
                        path, size, digest = store.attachment(room, parsed.path[len('/files/'):])
                        with path.open('rb') as stream:
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/octet-stream')
                            self.send_header('Content-Length', str(size))
                            self.send_header('X-Content-SHA256', digest)
                            self.send_header('Cache-Control', 'no-store')
                            self.end_headers()
                            while True:
                                chunk = stream.read(65536)
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                    else:
                        self.json_reply(404, {'error': 'Unknown chat endpoint.'})
                except ConflictError as error:
                    self.json_reply(409, {'error': str(error)})
                except FileNotFoundError as error:
                    self.json_reply(404, {'error': str(error)})
                except (ValueError, TypeError, UnicodeError) as error:
                    self.json_reply(400, {'error': str(error)})
                except (OSError, sqlite3.Error):
                    # Do not expose host filesystem paths to clients.
                    try:
                        self.json_reply(503, {'error': 'Host storage or connection is unavailable.'})
                    except OSError:
                        pass

            def do_GET(self):
                self.dispatch('GET')

            def do_POST(self):
                self.dispatch('POST')

        self.httpd = ThreadingHTTPServer((bind, int(port)), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Chat redirects are disabled. Enter the final server address.')


class ChatClient:
    def __init__(self, address, token, room, author, timeout=20):
        self.address = address.strip().rstrip('/')
        parsed = urlsplit(self.address)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Enter an http:// or https:// chat server address without credentials or query parameters.')
        if parsed.scheme == 'http':
            try:
                ip = ipaddress.ip_address(parsed.hostname)
                local = ip.is_private or (ip.version == 4 and ip in ipaddress.ip_network('100.64.0.0/10'))
            except ValueError:
                local = parsed.hostname.lower() == 'localhost'
            if not local:
                raise ValueError('Use HTTPS for public servers, or an HTTP private LAN/VPN IP address.')
        self.room = field(room, 'room', 100)
        self.author = field(author, 'name', 80)
        self.token = field(token, 'access token', 256)
        if not self.token.isascii() or any(c.isspace() for c in self.token):
            raise ValueError('Access token must be ASCII without spaces.')
        self.timeout = timeout
        self.opener = build_opener(NoRedirect())

    def request(self, endpoint, query=None, data=None):
        query = dict(query or {}, room=self.room)
        url = self.address + endpoint + '?' + urlencode(query)
        request = Request(url, data=data, headers={'Authorization': 'Bearer ' + self.token,
                                                  'Content-Type': 'application/json' if endpoint == '/messages' else 'application/octet-stream'})
        try:
            return self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            try:
                detail = json.loads(error.read(MAX_JSON)).get('error', '')
            except (ValueError, AttributeError):
                detail = ''
            raise RuntimeError(detail or f'Chat server returned HTTP {error.code}.') from None
        except URLError as error:
            raise RuntimeError(f'Cannot reach chat server: {error.reason}') from None

    def json_request(self, *args, **kwargs):
        with self.request(*args, **kwargs) as response:
            data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError('Chat server response is too large.')
        return json.loads(data)

    def messages(self, after=0):
        result = self.json_request('/messages', {'after': after})
        rows = result.get('messages')
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('Invalid chat history response.')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= after:
                raise ValueError('Invalid message ID.')
            field(row.get('author'), 'sender', 80)
            if not isinstance(row.get('text'), str) or len(row['text']) > 8000:
                raise ValueError('Invalid chat message.')
            if row.get('file_id'):
                if not re.fullmatch(r'[0-9a-f]{32}', row['file_id']):
                    raise ValueError('Invalid attachment ID.')
                attachment_name(row.get('name'))
                if type(row.get('size')) is not int or not 0 <= row['size'] <= MAX_FILE:
                    raise ValueError('Invalid attachment size.')
        return rows

    def send(self, text):
        text = field(text, 'message', 8000)
        return self.json_request('/messages', data=json.dumps({'author': self.author, 'text': text}).encode())

    def upload(self, path):
        path = Path(path)
        if path.stat().st_size > MAX_FILE:
            raise ValueError('Attachments must be at most 50 MiB.')
        with path.open('rb') as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError('Attachments must be at most 50 MiB.')
        return self.json_request('/files', {'author': self.author, 'name': attachment_name(path.name)}, data=data)

    def download(self, item, destination):
        destination = Path(destination)
        temporary = None
        try:
            with self.request('/files/' + item['file_id']) as response:
                length = int(response.headers.get('Content-Length', '-1'))
                if length != item['size'] or not 0 <= length <= MAX_FILE:
                    raise ValueError('Attachment size does not match the chat record.')
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.team-download-', delete=False) as output:
                    temporary = Path(output.name)
                    digest = hashlib.sha256()
                    remaining = length
                    while remaining:
                        chunk = response.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError('Attachment download was interrupted.')
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if digest.hexdigest() != item.get('sha256'):
                    raise ValueError('Attachment checksum did not match.')
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Pocket Editor team chat host; use a LAN/VPN or an HTTPS proxy.')
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--token-env', default='POCKET_TEAM_TOKEN')
    parser.add_argument('--project', type=Path)
    parser.add_argument('--room', default='project')
    args = parser.parse_args()
    token = os.environ.get(args.token_env, '')
    if not token:
        parser.error('Set the access token in ' + args.token_env + ' before starting the host.')
    server = ChatServer(args.storage, token, args.bind, args.port, args.project, args.room)
    print(f'Team chat listening on {args.bind}:{server.port}; Ctrl+C stops the host.', flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.close()


if __name__ == '__main__':
    main()
