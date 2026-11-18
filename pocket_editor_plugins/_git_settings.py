"""Read known Git settings and save a repository-local configuration atomically."""
import os
from pathlib import Path
import tempfile

FIELDS = ('user.name', 'user.email', 'remote.origin.url', 'core.autocrlf', 'core.filemode', 'commit.gpgsign')
CHOICES = {'core.autocrlf': ('', 'false', 'true', 'input'), 'core.filemode': ('', 'false', 'true'),
           'commit.gpgsign': ('', 'false', 'true')}


def parse_config(data):
    result = {}
    for record in data.split(b'\0'):
        if not record:
            continue
        key, separator, value = record.partition(b'\n')
        name = key.decode('utf-8', 'replace').lower()
        if name in (*FIELDS, 'remote.origin.fetch'):
            result[name] = value.decode('utf-8', 'replace') if separator else 'true'
    return result


def normalize(values):
    result = {}
    for key in FIELDS:
        value = values.get(key, '')
        if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in '\r\n\0'):
            raise ValueError('Invalid Git setting: ' + key)
        value = value.strip()
        if key in CHOICES and value not in CHOICES[key]:
            raise ValueError('Invalid choice for ' + key)
        if key == 'remote.origin.url' and value.startswith('-'):
            raise ValueError('Remote URL cannot start with a dash.')
        result[key] = value
    return result


def read_settings(runner, folder):
    local, _ = runner.run(folder, ['config', '--local', '--null', '--list'], timeout=10)
    effective, _ = runner.run(folder, ['config', '--null', '--list'], timeout=10)
    local = parse_config(local)
    for key in CHOICES:
        if key in local:
            value = local[key].lower()
            if value in ('yes', 'on', '1'):
                local[key] = 'true'
            elif value in ('', 'no', 'off', '0'):
                local[key] = 'false'
    return {'local': {key: local.get(key, '') for key in FIELDS}, 'effective': parse_config(effective)}


def save_settings(runner, folder, values):
    values = normalize(values)
    common, _ = runner.run(folder, ['rev-parse', '--git-common-dir'], timeout=10)
    directory = Path(os.fsdecode(common).strip())
    if not directory.is_absolute():
        directory = Path(folder) / directory
    original = directory / 'config'
    target = original.resolve()
    lock = original.with_name('config.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('Git configuration is locked by another operation. Try again when it finishes.') from None
    temporary = None
    try:
        os.close(descriptor)
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.pocket-git-config-', delete=False) as stream:
            temporary = Path(stream.name)
            if target.exists():
                stream.write(target.read_bytes())
        raw, _ = runner.run(folder, ['config', '--file', str(temporary), '--null', '--list'], timeout=10)
        previous = parse_config(raw)
        for key, value in values.items():
            if value:
                runner.run(folder, ['config', '--file', str(temporary), '--replace-all', '--', key, value], timeout=10)
            elif key in previous:
                runner.run(folder, ['config', '--file', str(temporary), '--unset-all', '--', key], timeout=10)
        if values['remote.origin.url'] and 'remote.origin.fetch' not in previous:
            runner.run(folder, ['config', '--file', str(temporary), '--replace-all', '--', 'remote.origin.fetch',
                                '+refs/heads/*:refs/remotes/origin/*'], timeout=10)
        os.chmod(temporary, mode)
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
            temporary.with_name(temporary.name + '.lock').unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    return read_settings(runner, folder)
