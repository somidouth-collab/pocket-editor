#!/usr/bin/env python3
"""Build a curated PyPI sdist and wheel. Requires the Python build package."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from build_release import ROOT, collect_files


def stage_project(destination, root=ROOT):
    root, destination = Path(root), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "README.md", "MANIFEST.in"):
        shutil.copy2(root / "packaging" / name, destination / name)
    package = destination / "pocket_editor_app"
    shutil.copytree(root / "packaging/pocket_editor_app", package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    payload = package / "_payload"
    for source in collect_files(root):
        relative = source.relative_to(root)
        if relative.parts[0] in ("packaging", ".github") or relative.name.startswith("test_") or relative.name == ".gitignore":
            continue
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/pypi")
    parser.add_argument("--no-isolation", action="store_true", help="use dependencies already installed in a dedicated build environment")
    args = parser.parse_args()
    output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix="pocket-editor-pypi-") as directory:
        stage = stage_project(Path(directory) / "source")
        command = [sys.executable, "-m", "build", "--outdir", str(output)]
        if args.no_isolation:
            command.append("--no-isolation")
        subprocess.run(command + [str(stage)], check=True)


if __name__ == "__main__":
    main()
