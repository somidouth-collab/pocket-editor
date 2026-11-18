"""Portable per-client invitation files. Importing a file never connects or executes code."""
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from pocket_editor_plugins._team_chat import ChatClient, field

MAX_INVITATION = 16384


def validate_invitation(data):
    if not isinstance(data, dict) or data.get('format') != 'pocket-team-invite' or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('Invalid or unsupported team invitation file.')
    address = field(data.get('server'), 'server address', 2000)
    room = field(data.get('room'), 'room', 100)
    name = field(data.get('client_name'), 'client name', 80)
    key = field(data.get('client_key'), 'client key', 256)
    if len(key) < 16:
        raise ValueError('The invitation client key is too short.')
    # Constructor validation is offline: it does not contact the host.
    ChatClient(address, key, room, name)
    parsed = urlsplit(address)
    parsed.port  # Validate the port without resolving or contacting the host.
    if parsed.hostname in ('0.0.0.0', '::'):
        raise ValueError('Use a client-reachable server address, not a wildcard bind address.')
    project = field(data.get('project_name', 'Shared project'), 'project name', 200)
    return {'format': 'pocket-team-invite', 'version': 1, 'server': address.rstrip('/'),
            'room': room, 'client_name': name, 'client_key': key, 'project_name': project}


def read_invitation(filename):
    with Path(filename).open('rb') as stream:
        raw = stream.read(MAX_INVITATION + 1)
    if len(raw) > MAX_INVITATION:
        raise ValueError('The invitation file is too large.')
    return validate_invitation(json.loads(raw.decode('utf-8')))


def write_invitation(filename, data):
    data = validate_invitation(data)
    path = Path(filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.team-invite-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(data, indent=2) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
