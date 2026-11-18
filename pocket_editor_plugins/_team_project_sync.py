"""Authoritative host folder and conflict-aware client copies."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import threading

MAX_FILE = 50 * 1024 * 1024
MAX_PROJECT = 512 * 1024 * 1024
MAX_FILES = 5000
EXCLUDED = {'.git', '.hg', '.svn', '.ssh', '.aws', '.venv', 'venv', '__pycache__',
            'node_modules', '.team-sync', '.DS_Store', 'id_rsa', 'id_ed25519'}


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
    def __init__(self, root, storage, guard=None):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('Choose an existing host project folder.')
        self.storage = Path(storage).resolve()
        if self.storage.is_relative_to(self.root):
            raise ValueError('Host chat and history storage must be outside the shared project.')
        self.backups = self.storage / 'project-versions'
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.project_id = hashlib.sha256(os.fsencode(str(self.root))).hexdigest()
        self.lock = threading.Lock()
        self.guard = guard

    def manifest(self):
        with self.lock:
            return {'available': True, 'project_id': self.project_id, 'files': file_list(self.root)}

    def read(self, name, expected):
        with self.lock:
            data = read_file(safe_path(self.root, name))
            if data is None:
                raise FileNotFoundError('Project file no longer exists. Pull again.')
            if digest(data) != expected:
                raise ConflictError('Project changed during transfer. Pull again.')
            return data

    def write(self, name, expected, data):
        if len(data) > MAX_FILE:
            raise ValueError('Project files must be at most 50 MiB.')
        with self.lock:
            path = safe_path(self.root, name)
            if self.guard is not None and not self.guard(name):
                raise ConflictError(f'Conflict in {name}: the host has this file open. Ask the host to save and close its tab.')
            previous = read_file(path)
            if digest(previous) != expected:
                raise ConflictError(f'Conflict in {name}: the server has a newer version. Pull and reconcile your copy.')
            entries = file_list(self.root)
            total = sum(item['size'] for item in entries) - len(previous or b'') + len(data)
            if total > MAX_PROJECT or (previous is None and len(entries) >= MAX_FILES):
                raise ValueError('Shared project storage limit reached.')
            if previous is not None:
                backup = self.backups / digest(previous)
                if not backup.exists():
                    used = sum(item.stat().st_size for item in self.backups.iterdir() if item.is_file())
                    if used + len(previous) > 1024 * 1024 * 1024:
                        raise ValueError('Host version history is full (1 GiB).')
                    atomic_file(backup, previous)
            atomic_file(path, data)
            return {'path': name, 'size': len(data), 'sha256': digest(data)}


class ProjectCopy:
    def __init__(self, client, folder, storage):
        self.client = client
        self.root = Path(folder).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError('Choose an existing local project folder.')
        key = hashlib.sha256((client.address + '\0' + client.room + '\0' + str(self.root)).encode()).hexdigest()
        self.state_file = Path(storage) / 'client-copies' / (key + '.json')
        self.project_id = None
        self.base = {}
        try:
            saved = json.loads(self.state_file.read_text())
            self.project_id, self.base = saved['project_id'], saved['files']
            if not isinstance(self.base, dict):
                raise ValueError('Invalid copy state.')
        except FileNotFoundError:
            pass

    def save(self):
        atomic_file(self.state_file, json.dumps({'project_id': self.project_id, 'files': self.base}).encode())

    def manifest(self):
        result = self.client.json_request('/project/manifest')
        if not result.get('available'):
            raise ValueError('This host has not selected a shared project folder.')
        files = result.get('files')
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise ValueError('Invalid shared project manifest.')
        seen = set()
        total = 0
        for item in files:
            shared_path(item['path'])
            if item['path'] in seen or type(item['size']) is not int or not 0 <= item['size'] <= MAX_FILE:
                raise ValueError('Invalid shared project file.')
            if not isinstance(item['sha256'], str) or len(item['sha256']) != 64 or any(c not in '0123456789abcdef' for c in item['sha256']):
                raise ValueError('Invalid project file checksum.')
            seen.add(item['path'])
            total += item['size']
        if total > MAX_PROJECT:
            raise ValueError('Shared project exceeds 512 MiB.')
        if result['project_id'] != self.project_id:
            self.project_id = result['project_id']
            self.base = {}
        return files

    def pull(self):
        files = self.manifest()
        copied, conflicts = [], []
        for item in files:
            name = item['path']
            path = safe_path(self.root, name)
            before = digest(read_file(path))
            if before == item['sha256']:
                self.base[name] = before
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
            self.save()
            copied.append(name)
        self.save()
        return {'copied': copied, 'conflicts': conflicts}

    def publish(self):
        remote = {item['path']: item for item in self.manifest()}
        sent, conflicts = [], []
        for item in file_list(self.root):
            name = item['path']
            current = remote.get(name, {}).get('sha256')
            if current == item['sha256']:
                self.base[name] = current
                continue
            baseline = self.base.get(name)
            if item['sha256'] == baseline:
                continue  # Unmodified local copy; newer server work must be pulled.
            if current != baseline:
                conflicts.append(name)
                continue
            data = read_file(safe_path(self.root, name))
            if data is None:
                continue
            try:
                self.client.json_request('/project/file', {'path': name, 'base': baseline or '-'}, data=data)
            except RuntimeError as error:
                if 'Conflict' in str(error):
                    conflicts.append(name)
                    continue
                raise
            self.base[name] = digest(data)
            self.save()
            sent.append(name)
        self.save()
        return {'sent': sent, 'conflicts': conflicts}
