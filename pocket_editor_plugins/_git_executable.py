"""Locate and validate installed Git, including Git for Windows outside PATH."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SETTINGS_PATH = Path(__file__).with_name('git_executable_settings.json')
DOWNLOAD_URL = 'https://git-scm.com/install/windows'


def saved_executable():
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        value = data.get('executable', '')
        return value if isinstance(value, str) else ''
    except (OSError, ValueError, TypeError, AttributeError):
        return ''


def candidates(preferred='', platform=None, environment=None):
    platform = sys.platform if platform is None else platform
    environment = os.environ if environment is None else environment
    if preferred:
        yield Path(preferred).expanduser()
        return  # An explicitly selected executable must never silently fall back.
    found = shutil.which('git', path=environment.get('PATH', ''))
    if found:
        yield Path(found)
    if platform != 'win32':
        return
    roots = []
    for variable, suffix in [('ProgramFiles', 'Git'), ('ProgramW6432', 'Git'),
                             ('ProgramFiles(x86)', 'Git'), ('LOCALAPPDATA', 'Programs/Git')]:
        if environment.get(variable):
            roots.append(Path(environment[variable]) / suffix)
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r'Software\GitForWindows', 0, winreg.KEY_READ | view) as key:
                        roots.append(Path(winreg.QueryValueEx(key, 'InstallPath')[0]))
                except OSError:
                    pass
    except ImportError:
        pass
    for root in roots:
        yield root / 'cmd/git.exe'
        yield root / 'bin/git.exe'


def find_git(preferred=''):
    for path in candidates(preferred):
        if path.is_file():
            return str(path.resolve())
    if preferred:
        raise RuntimeError('The selected Git executable no longer exists. Open Git setup and choose git.exe again.')
    raise RuntimeError('Git was not found. Open Git Project → Git setup to detect installed Git, choose git.exe, or install Git for Windows.')


def process_options():
    return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)} if sys.platform == 'win32' else {}


def verify_git(preferred=''):
    path = find_git(preferred)
    try:
        result = subprocess.run([path, '--version'], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=15,
                                stdin=subprocess.DEVNULL, **process_options())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('Cannot run the selected Git executable. Choose a working Git installation.') from error
    version = result.stdout.strip()
    if result.returncode or not version.lower().startswith('git version '):
        raise RuntimeError('The selected program did not report a Git version. Choose git.exe from a Git installation.')
    return path, version


def install_windows_git():
    if sys.platform != 'win32':
        raise RuntimeError('This install button is for Windows. Install Git with your operating system package manager.')
    winget = shutil.which('winget')
    if not winget:
        raise RuntimeError('Windows Package Manager (winget) is unavailable. Use Open official installer, install Git, then click Detect installed Git.')
    command = [winget, 'install', '--id', 'Git.Git', '-e', '--source', 'winget',
               '--accept-package-agreements', '--accept-source-agreements']
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=600, stdin=subprocess.DEVNULL,
                                **process_options())
    except subprocess.TimeoutExpired:
        raise RuntimeError('Git installation exceeded ten minutes. Check Windows installation prompts and try Detect installed Git.') from None
    if result.returncode:
        raise RuntimeError('Windows could not complete Git installation. Use the official installer, then Detect installed Git.\n' + (result.stderr or result.stdout)[-2000:])
    return verify_git()
