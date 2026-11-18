"""Launch the portable app from a writable per-user runtime copy."""
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile

from . import __version__


def runtime_root():
    override = os.environ.get("POCKET_EDITOR_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "pocket-editor" / "pypi"


def prepare_runtime():
    payload = Path(__file__).resolve().parent / "_payload"
    if not (payload / "pocket_editor.py").is_file():
        raise RuntimeError("Pocket Editor package is incomplete; reinstall it from PyPI.")
    base = runtime_root()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = base / __version__
    if target.exists():
        if not (target / "pocket_editor.py").is_file():
            raise RuntimeError("Pocket Editor runtime is incomplete: " + str(target))
        return target
    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=base))
    try:
        shutil.copytree(payload, temporary, dirs_exist_ok=True)
        try:
            temporary.rename(target)
        except OSError:
            if not (target / "pocket_editor.py").is_file():
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def main():
    runtime = prepare_runtime()
    sys.path.insert(0, str(runtime))
    try:
        runpy.run_path(str(runtime / "pocket_editor.py"), run_name="__main__")
    except ModuleNotFoundError as error:
        if error.name in ("tkinter", "_tkinter"):
            raise SystemExit("Tkinter is required. On Debian/Ubuntu run: sudo apt install python3-tk") from error
        raise
    finally:
        sys.path.remove(str(runtime))
