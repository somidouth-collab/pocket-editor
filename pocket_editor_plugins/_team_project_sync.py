"""Authoritative host folder and conflict-aware client copies."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import tempfile
import threading

MAX_FILE = 50 * 1024 * 1024
MAX_PROJECT = 512 * 1024 * 1024
MAX_FILES = 5000
EXCLUDED = {'.git', '.hg', '.svn', '.ssh', '.aws', '.venv', 'venv', '__pycache__',
            'node_modules', '.pocket-debug', '.team-sync', '.DS_Store', 'id_rsa', 'id_ed25519'}


class ConflictError(ValueError):
    pass


def shared_path(value):
    if not isinstance(value, str) or not value or len(value) > 1000 or '\\' in value or ':' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid shared project path.')
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts) or PurePosixPath(value).is_absolute():
        raise ValueError('Shared paths must stay inside the project folder.')
    for part in parts:
        if part in EXCLUDED or part.startswith('.env') or part.endswith(('.pem', '.key')):
            raise ValueError('This private or generated path is excluded from project sharing.')
    return parts


def safe_path(root, name):
    path = Path(root)
    for part in shared_path(name):
        path = path / part
        if path.is_symlink():
            raise ValueError('Symbolic links cannot be shared or replaced.')
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Shared path escapes the project.')
    return path


def read_file(path):
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('A project file path points to a directory.')
    if path.stat().st_size > MAX_FILE:
        raise ValueError('Project files must be at most 50 MiB.')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError('Project files must be at most 50 MiB.')
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def atomic_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.team-sync-', delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def file_list(root):
    entries = []
    total = 0
    for directory, folders, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        allowed = []
        for name in sorted(folders):
            candidate = (relative / name).as_posix()
            try:
                safe_path(root, candidate)
            except ValueError:
                continue
            allowed.append(name)
        folders[:] = allowed
        for name in sorted(files):
            candidate = (relative / name).as_posix()
            if name.startswith('.team-sync-'):
                continue
            try:
                path = safe_path(root, candidate)
            except ValueError:
                continue
            data = read_file(path)
            if data is None:
                continue
            total += len(data)
            if total > MAX_PROJECT or len(entries) >= MAX_FILES:
                raise ValueError('Shared project limit is 5000 files / 512 MiB. Choose a smaller project folder.')
            entries.append({'path': candidate, 'size': len(data), 'sha256': digest(data)})
    return entries


class ProjectStore:
    """Host-authoritative file index with immutable, deduplicated revisions."""
    def __init__(self, root, storage, guard=None):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('Choose an existing host project folder.')
        self.storage = Path(storage).resolve()
        if self.storage.is_relative_to(self.root):
            raise ValueError('Host chat and history storage must be outside the shared project.')
        # Retain the old history location so existing backups remain available.
        self.backups = self.storage / 'project-versions'
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.storage / 'project.sqlite3'
        self.project_id = hashlib.sha256(os.fsencode(str(self.root))).hexdigest()
        self.lock = threading.Lock()
        self.guard = guard
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS project_files ('
                       'project TEXT, path TEXT, sha256 TEXT, size INTEGER, version INTEGER, '
                       'author TEXT, changed REAL, PRIMARY KEY(project,path))')
            db.execute('CREATE TABLE IF NOT EXISTS project_history ('
                       'project TEXT, path TEXT, version INTEGER, sha256 TEXT, size INTEGER, '
                       'author TEXT, changed REAL, action TEXT, PRIMARY KEY(project,path,version))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()

    def _blob(self, data):
        checksum = digest(data)
        target = self.backups / checksum
        if target.is_symlink():
            raise ValueError('Invalid project content storage.')
        if not target.exists():
            used = sum(item.stat().st_size for item in self.backups.iterdir() if item.is_file())
            if used + len(data) > 1024 * 1024 * 1024:
                raise ValueError('Host content and version history is full (1 GiB).')
            atomic_file(target, data)
        elif digest(read_file(target)) != checksum:
            raise ValueError('Host content storage failed its checksum check.')
        return checksum

    def _entries(self, db):
        return {row['path']: dict(row) for row in db.execute(
            'SELECT path,sha256,size,version,author,changed FROM project_files WHERE project=?', (self.project_id,))}

    def _record(self, db, name, checksum, size, previous, author):
        version = previous['version'] + 1 if previous else 1
        changed = time.time()
        action = 'delete' if checksum is None else 'create' if not previous or previous['sha256'] is None else 'update'
        values = (self.project_id, name, checksum, size, version, author, changed)
        db.execute('INSERT OR REPLACE INTO project_files VALUES (?,?,?,?,?,?,?)', values)
        db.execute('INSERT INTO project_history VALUES (?,?,?,?,?,?,?,?)',
                   (self.project_id, name, version, checksum, size, author, changed, action))
        return dict(path=name, sha256=checksum, size=size, version=version, author=author, changed=changed)

    def _refresh(self, db):
        """Index saved host edits; unsaved editor drafts never become shared data."""
        entries = self._entries(db)
        present = set()
        for item in file_list(self.root):
            name = item['path'];present.add(name)
            previous = entries.get(name)
            if previous and previous['sha256'] == item['sha256']:
                continue
            data = read_file(safe_path(self.root, name))
            if data is None or digest(data) != item['sha256']:
                raise ConflictError('Host project changed while indexing. Try again.')
            self._blob(data)
            entries[name] = self._record(db, name, item['sha256'], item['size'], previous, 'Host')
        for name, previous in list(entries.items()):
            if name not in present and previous['sha256'] is not None:
                entries[name] = self._record(db, name, None, 0, previous, 'Host')
        return entries

    def manifest(self, known=None, project_id=None, prefixes=None):
        if known is not None:
            if not isinstance(known, dict) or len(known) > MAX_FILES:
                raise ValueError('Invalid client manifest.')
            for name, item in known.items():
                shared_path(name)
                if (not isinstance(item, dict) or not valid_hash(item.get('sha256')) or
                        type(item.get('version')) is not int or item['version'] < 1):
                    raise ValueError('Invalid client manifest revision.')
        with self.lock, self.connect() as db:
            entries = self._refresh(db)
            full = known is None or project_id != self.project_id
            allowed = lambda name: not prefixes or any(name == prefix or name.startswith(prefix + '/') for prefix in prefixes)
            if known is not None and any(not allowed(name) for name in known):
                raise ValueError('Client manifest exceeds allowed project folders.')
            active = {name: item for name, item in entries.items() if item['sha256'] is not None and allowed(name)}
            changed = [item for name, item in active.items() if full or
                       known.get(name) != {'sha256': item['sha256'], 'version': item['version']}]
            deleted = [{'path': name, 'version': entries.get(name, {}).get('version', 0)}
                       for name in (known or {}) if name not in active] if not full else []
            return dict(available=True, protocol=2, project_id=self.project_id, full=full,
                        files=changed, deleted=deleted)

    def read(self, name, expected):
        with self.lock, self.connect() as db:
            self._refresh(db)
            data = read_file(safe_path(self.root, name))
            if data is None:
                raise FileNotFoundError('Project file no longer exists. Pull again.')
            if digest(data) != expected:
                raise ConflictError('Project changed during transfer. Pull again.')
            return data

    def write(self, name, expected, data, version=None, author='Host'):
        if len(data) > MAX_FILE:
            raise ValueError('Project files must be at most 50 MiB.')
        if version is not None and (type(version) is not int or version < 0):
            raise ValueError('A nonnegative base version is required.')
        with self.lock, self.connect() as db:
            path = safe_path(self.root, name)
            if self.guard is not None and not self.guard(name):
                raise ConflictError(f'Conflict in {name}: the host has this file open. Ask the host to save and close its tab.')
            entries = self._refresh(db)
            previous_entry = entries.get(name)
            previous = read_file(path)
            if (digest(previous) != expected or
                    (version is not None and version != (previous_entry['version'] if previous_entry else 0))):
                raise ConflictError(f'Conflict in {name}: the server has a newer version. Pull and reconcile your copy.')
            total = sum(item['size'] for item in entries.values()) - len(previous or b'') + len(data)
            count = sum(item['sha256'] is not None for item in entries.values())
            if total > MAX_PROJECT or (previous is None and count >= MAX_FILES):
                raise ValueError('Shared project storage limit reached.')
            checksum = self._blob(data)
            if previous_entry and checksum == previous_entry['sha256']:
                return previous_entry
            atomic_file(path, data)
            return self._record(db, name, checksum, len(data), previous_entry, author)

    def history(self, name):
        shared_path(name)
        with self.lock, self.connect() as db:
            self._refresh(db)
            return [dict(row) for row in db.execute(
                'SELECT path,version,sha256,size,author,changed,action FROM project_history '
                'WHERE project=? AND path=? ORDER BY version DESC LIMIT 100', (self.project_id, name))]


def valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class ProjectCopy:
    def __init__(self, client, folder, storage):
        self.client = client
        self.root = Path(folder).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('Choose an existing local project folder.')
        key = hashlib.sha256((client.address + '\0' + client.room + '\0' + str(self.root)).encode()).hexdigest()
        self.state_file = Path(storage) / 'client-copies' / (key + '.json')
        self.conflict_folder = Path(storage) / 'client-conflicts' / key
        self.project_id = None
        self.base = {}
        self.remote = {}
        self.versions = {}
        self.protocol = 1
        try:
            saved = json.loads(self.state_file.read_text())
            self.project_id, self.base = saved['project_id'], saved['files']
            self.remote = saved.get('remote', {})
            self.versions = saved.get('versions', {})
            if not isinstance(self.remote, dict) or not isinstance(self.versions, dict):
                raise ValueError('Invalid copy revision state.')
            if not isinstance(self.base, dict):
                raise ValueError('Invalid copy state.')
        except FileNotFoundError:
            pass

    def save(self):
        atomic_file(self.state_file, json.dumps({'project_id': self.project_id, 'files': self.base, 'remote': self.remote, 'versions': self.versions}).encode())

    def manifest(self):
        # Negotiate once with old hosts, then exchange only last-known revisions.
        if self.protocol >= 2:
            known = {name: {'sha256': item['sha256'], 'version': item['version']}
                     for name, item in self.remote.items()}
            payload = json.dumps({'project_id': self.project_id, 'files': known}).encode()
            result = self.client.json_request('/project/manifest', data=payload)
        else:
            result = self.client.json_request('/project/manifest')
        if not isinstance(result, dict):
            raise ValueError('Invalid shared project response.')
        if type(result.get('protocol', 1)) is not int or result.get('protocol', 1) not in (1, 2):
            raise ValueError('Unsupported shared project protocol.')
        if not result.get('available'):
            raise ValueError('This host has not selected a shared project folder.')
        files = result.get('files')
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise ValueError('Invalid shared project manifest.')
        seen = set()
        total = 0
        for item in files:
            if not isinstance(item, dict) or not {'path', 'size', 'sha256'} <= item.keys():
                raise ValueError('Invalid shared project file.')
            safe_path(self.root, item['path'])
            if item['path'] in seen or type(item['size']) is not int or not 0 <= item['size'] <= MAX_FILE:
                raise ValueError('Invalid shared project file.')
            if not valid_hash(item['sha256']):
                raise ValueError('Invalid project file checksum.')
            if result.get('protocol', 1) >= 2 and (type(item.get('version')) is not int or item['version'] < 1):
                raise ValueError('Invalid shared project version.')
            seen.add(item['path'])
            total += item['size']
        if total > MAX_PROJECT:
            raise ValueError('Shared project exceeds 512 MiB.')
        if not valid_hash(result.get('project_id')):
            raise ValueError('Invalid shared project identity.')
        new_project = result['project_id'] != self.project_id
        protocol = result.get('protocol', 1)
        versions = {} if new_project else dict(self.versions)
        merged = {}
        if protocol >= 2:
            if type(result.get('full')) is not bool or (new_project and not result['full']):
                raise ValueError('A complete manifest is required for a new project.')
            merged = {} if result['full'] else dict(self.remote)
            deleted = result.get('deleted', [])
            if not isinstance(deleted, list) or len(deleted) > MAX_FILES:
                raise ValueError('Invalid removed file manifest.')
            removed_names = set()
            for item in deleted:
                if not isinstance(item, dict) or not {'path', 'version'} <= item.keys():
                    raise ValueError('Invalid removed file manifest.')
                name = item['path'];shared_path(name)
                if name in removed_names or name in seen:
                    raise ValueError('Duplicate shared project path.')
                removed_names.add(name)
                if type(item.get('version')) is not int or item['version'] < 0:
                    raise ValueError('Invalid removed file version.')
                merged.pop(name, None)
                versions[name] = item['version']
            merged.update({item['path']: item for item in files})
            if len(merged) > MAX_FILES or sum(item['size'] for item in merged.values()) > MAX_PROJECT:
                raise ValueError('Shared project exceeds its limits.')
        # Commit negotiated state only after the entire response passes validation.
        if new_project:self.base = {}
        self.project_id = result['project_id']
        self.protocol = protocol
        for item in merged.values():
            if item['path'] not in versions and item['sha256'] == self.base.get(item['path']):
                versions[item['path']] = item['version']
        self.versions = versions
        self.remote = merged
        return list(merged.values()) if protocol >= 2 else files

    def preserve_conflicts(self, names):
        copies = {}
        for name in names:
            data = read_file(safe_path(self.root, name))
            if data is None:continue
            # Use opaque names so untrusted remote paths never become backup paths.
            ident = hashlib.sha256(name.encode()).hexdigest() + '-' + digest(data)
            target = self.conflict_folder / ident
            for parent in (self.conflict_folder.parent, self.conflict_folder, target):
                if parent.is_symlink():raise ValueError('Invalid local conflict backup path.')
            if not target.exists():atomic_file(target, data)
            atomic_file(target.with_suffix('.json'), json.dumps({'path': name, 'sha256': digest(data),
                        'base_version': self.versions.get(name, 0), 'created': time.time()}).encode())
            copies[name] = str(target)
        return copies

    def pull(self):
        files = self.manifest()
        copied, conflicts = [], []
        for item in files:
            name = item['path']
            path = safe_path(self.root, name)
            before = digest(read_file(path))
            if before == item['sha256']:
                self.base[name] = before
                self.versions[name] = item.get('version', 0)
                continue
            baseline = self.base.get(name)
            if before is not None and before != baseline:
                if item['sha256'] != baseline:
                    conflicts.append(name)
                continue
            with self.client.request('/project/file', {'path': name, 'sha256': item['sha256']}) as response:
                data = response.read(MAX_FILE + 1)
            if len(data) != item['size'] or digest(data) != item['sha256']:
                raise ValueError('Project file transfer failed its checksum check.')
            if digest(read_file(safe_path(self.root, name))) != before:
                conflicts.append(name)
                continue
            atomic_file(path, data)
            self.base[name] = item['sha256']
            self.versions[name] = item.get('version', 0)
            self.save()
            copied.append(name)
        self.save()
        return {'copied': copied, 'conflicts': conflicts, 'conflict_copies': self.preserve_conflicts(conflicts)}

    def publish(self):
        remote = {item['path']: item for item in self.manifest()}
        sent, conflicts = [], []
        for item in file_list(self.root):
            name = item['path']
            current = remote.get(name, {}).get('sha256')
            if current == item['sha256']:
                self.base[name] = current
                self.versions[name] = remote[name].get('version', 0)
                continue
            baseline = self.base.get(name)
            if item['sha256'] == baseline:
                continue  # Unmodified local copy; newer server work must be pulled.
            if current != baseline or (self.protocol >= 2 and remote.get(name, {}).get('version', 0) != self.versions.get(name, 0)):
                conflicts.append(name)
                continue
            data = read_file(safe_path(self.root, name))
            if data is None:
                continue
            try:
                query = {'path': name, 'base': baseline or '-'}
                if self.protocol >= 2:query['version'] = str(self.versions.get(name, 0))
                accepted = self.client.json_request('/project/file', query, data=data)
            except RuntimeError as error:
                if 'Conflict' in str(error):
                    conflicts.append(name)
                    continue
                raise
            self.base[name] = digest(data)
            if self.protocol >= 2:
                if not valid_hash(accepted.get('sha256')) or accepted['sha256'] != digest(data) or type(accepted.get('version')) is not int or accepted['version'] < 1:
                    raise ValueError('Host returned an invalid upload revision.')
                self.versions[name] = accepted['version']
                self.remote[name] = accepted
            self.save()
            sent.append(name)
        self.save()
        return {'sent': sent, 'conflicts': conflicts, 'conflict_copies': self.preserve_conflicts(conflicts)}
