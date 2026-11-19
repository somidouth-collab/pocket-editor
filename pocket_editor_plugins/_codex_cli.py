"""Codex CLI discovery and cancellable execution without a shell."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading


def cli_prefix(selected=''):
    executable = selected.strip() or shutil.which('codex') or ''
    if not executable and sys.platform == 'win32':
        candidate = Path(os.environ.get('APPDATA', '')) / 'npm/codex.cmd'
        if candidate.is_file(): executable = str(candidate)
    if not executable:
        raise RuntimeError('Codex CLI was not found. Install it with npm install -g @openai/codex, then restart the editor or browse to its executable.')
    path = Path(executable).expanduser()
    if not path.is_file(): raise RuntimeError('The selected Codex executable does not exist.')
    if path.suffix.lower() in ('.cmd', '.bat'):
        script = path.parent / 'node_modules/@openai/codex/bin/codex.js'
        node = shutil.which('node')
        if not script.is_file() or not node:
            raise RuntimeError('Choose codex.exe or the npm Codex installation with Node.js available. Shell command shims are not executed directly.')
        return [node, str(script)]
    if path.suffix.lower() == '.js':
        node = shutil.which('node')
        if not node: raise RuntimeError('Node.js is required for the selected Codex script.')
        return [node, str(path)]
    return [str(path)]


def command(prefix, folder, output, allow_edits=False, model=''):
    result = [*prefix, '-a', 'never', 'exec', '--sandbox', 'workspace-write' if allow_edits else 'read-only',
              '--skip-git-repo-check', '--color', 'never', '--json', '--cd', str(folder),
              '--output-last-message', str(output)]
    if model.strip(): result += ['--model', model.strip()]
    return [*result, '-']


class CodexRunner:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.cancelled = threading.Event()
        self.closed = False

    def prepare(self):
        with self.lock:
            if self.closed or self.process is not None: raise RuntimeError("Codex is stopped or already running.")
            self.cancelled.clear()

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None: return
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                                   timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=2)
                    except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL)
            except (OSError, subprocess.TimeoutExpired):
                try: process.kill()
                except OSError: pass

    def run(self, selected, folder, prompt, allow_edits=False, model=''):
        prefix = cli_prefix(selected)
        if not folder or not Path(folder).is_dir(): raise ValueError('Choose an existing project folder.')
        if not prompt.strip(): raise ValueError('Enter a Codex request.')
        if len(prompt.encode('utf-8')) > 65536: raise ValueError('Prompt and attached tab exceed 64 KiB; use a smaller selection.')
        with self.lock:
            if self.closed: raise RuntimeError('Codex plugin has stopped.')
            if self.process is not None: raise RuntimeError('A Codex request is already running.')
        with tempfile.TemporaryDirectory(prefix='pocket-codex-') as directory, tempfile.TemporaryFile() as log:
            output = Path(directory) / 'reply.txt'
            options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)} if os.name == 'nt' else {'start_new_session': True}
            with self.lock:
                if self.closed or self.cancelled.is_set(): raise RuntimeError('Codex request stopped.')
                self.process = subprocess.Popen(command(prefix, folder, output, allow_edits, model),
                    stdin=subprocess.PIPE, stdout=log, stderr=log, **options)
                process = self.process
            try:
                try: process.communicate(prompt.encode('utf-8'), timeout=300)
                except subprocess.TimeoutExpired:
                    self.cancel(); process.communicate(timeout=15)
                    raise RuntimeError('Codex exceeded five minutes and was stopped. Use a smaller task or run it in the Codex terminal.') from None
                if self.cancelled.is_set(): raise RuntimeError('Codex request stopped. Changes already made in edit mode are retained.')
                if process.returncode:
                    raise RuntimeError('Codex could not finish. Run codex login status in a terminal; check model access, CLI configuration and sandbox support. The request was not retried.')
                if not output.is_file(): raise RuntimeError('Codex produced no final reply. Check the CLI version and login in a terminal.')
                with output.open('rb') as stream: data = stream.read(1048577)
                if len(data) > 1048576: raise RuntimeError('Codex reply exceeded 1 MiB. Ask for a shorter reply.')
                return data.decode('utf-8', 'replace')
            finally:
                with self.lock: self.process = None

    def close(self):
        self.closed = True
        self.cancel()
