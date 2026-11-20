# Pocket Editor

Created by **Somi Douth** · somidouth@gmail.com

A desktop code editor built with Python and Tkinter, with Project Explorer, Git integration, optional AI tools, and host/client team collaboration.

## Install and launch

Python 3.9 or newer and Tkinter are required. On Debian/Ubuntu:

```sh
sudo apt install python3-tk pipx
pipx install pocket-editor
pocket-editor
```

Alternatively, install in a virtual environment:

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install pocket-editor
.venv/bin/pocket-editor
```

Windows: install Python with Tcl/Tk enabled, then `py -m pip install pocket-editor`. macOS requires a Python installation with Tkinter. Windows and macOS execution has not yet been verified.

## Features

- Project Explorer with Open, Save and Load project controls.
- Git initialization, repository settings, commit, pull and push, with Windows Git detection and installer controls.
- Host/client project sharing with per-client invitation files.
- Group text chat and audio, video and file attachments.
- Optional internet relay and AI provider integration.
- One Python Tools plugin and one PHP Tools plugin with formatting, syntax checks and completion.
- AI Assistant with native Google Gemini, OpenAI Responses and compatible provider presets.
- Reference-inspired dark GUI and persistent vertical tabs for user-chosen plugins.
- Docked Explorer, AI, Codex, Git, Terminal and Team panels.
- Offline HTML help and an extensible Python plugin system.

The core uses the Python standard library. Git features require Git. PHP features require Node.js/npm and a separate optional dependency installation. AI services require your own provider configuration.

Application files are copied to a version-specific writable user directory on first launch, so plugins and settings do not write into Python site-packages. On Linux this is `$XDG_DATA_HOME/pocket-editor/pypi/2026.11.20`, defaulting to `~/.local/share/pocket-editor/pypi/2026.11.20`. Windows uses LocalAppData and macOS uses Application Support. `POCKET_EDITOR_HOME` overrides the base directory. Runtime settings are retained when reinstalling the same version; different versions have separate runtime settings.

- [Source](https://github.com/somidouth-collab/pocket-editor)
- [Installation guide](https://github.com/somidouth-collab/pocket-editor/blob/main/INSTALL.md)
- [Support and bug reports](https://github.com/somidouth-collab/pocket-editor/issues)
- [SourceForge downloads](https://sourceforge.net/projects/pocket-editor/)

The project owner has not yet selected a license.
