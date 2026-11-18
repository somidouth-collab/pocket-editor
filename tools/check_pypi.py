#!/usr/bin/env python3
"""Check built PyPI distributions for required resources and private files."""
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile


def check(directory):
    directory = Path(directory)
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Expected exactly one wheel and one source distribution"
    for path in wheels + sources:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                assert archive.testzip() is None
                names = archive.namelist()
        else:
            with tarfile.open(path) as archive:
                names = archive.getnames()
        for name in names:
            parts = PurePosixPath(name).parts
            assert not any(part in {"node_modules", "__pycache__", "audit", "testp", ".git"} for part in parts), name
            base = parts[-1]
            assert not base.startswith((".env", "pocket_editor_session")), name
            assert not base.endswith((".team-invite", "_settings.json", ".pyc")), name
            assert base != "pocket_editor_about.json", name
        for required in ["pocket_editor.py", "docs/index.html", "tools/ai/chat_ai.py",
                         "pocket_editor_plugins/team_project.py", "INSTALL.md"]:
            assert any(name.endswith("/pocket_editor_app/_payload/" + required) or
                       name == "pocket_editor_app/_payload/" + required for name in names), required
        print("Verified:", path.name)


if __name__ == "__main__":
    check(sys.argv[1] if len(sys.argv) > 1 else "dist/pypi")
