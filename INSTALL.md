# Install Pocket Editor

Created by Somi Douth · somidouth@gmail.com

Pocket Editor is a Python source release. It requires Python 3.9 or newer and Tkinter.

## Debian and Ubuntu

Install the core requirements:

```sh
sudo apt update
sudo apt install python3 python3-tk unzip
```

Download `Pocket-Editor-2026.11.22.zip` from the release page, then run:

```sh
unzip Pocket-Editor-2026.11.22.zip
cd Pocket-Editor-2026.11.22
python3 -B pocket_editor.py
```

Alternatively, clone the source:

```sh
git clone https://github.com/somidouth-collab/pocket-editor.git
cd pocket-editor
python3 -B pocket_editor.py
```

## Windows and macOS

Install Python with Tkinter support. Extract the source ZIP and open a terminal in its folder. On Windows run `py pocket_editor.py`; on macOS run `python3 pocket_editor.py`. These platforms have not yet been verified.

## Install or upgrade from PyPI

On Debian/Ubuntu:

```sh
sudo apt install python3-tk pipx
pipx ensurepath
pipx install pocket-editor
```

Open a new terminal, then run `pocket-editor`. If it is already installed, run `pipx upgrade pocket-editor`. To use Python formatting, run `pipx inject pocket-editor black`.

## Optional features

The core editor requires no pip packages. PHP tools require Node.js/npm and `npm ci --prefix tools/php --ignore-scripts`. Python formatting optionally uses Black. AI services require each user's own provider configuration. Open AI Assistant → How to use for a Gemini example. Team internet sharing offers relay installation in its GUI.

## Help and support

- Source and documentation: https://github.com/somidouth-collab/pocket-editor
- Downloads: https://github.com/somidouth-collab/pocket-editor/releases
- Report a bug or request a feature: https://github.com/somidouth-collab/pocket-editor/issues
- SourceForge project: https://sourceforge.net/projects/pocket-editor/

For bug reports, include your operating system, Python version, reproduction steps, and error message. Remove credentials and invitation keys before sharing logs.

## Linux keyboard compatibility

The local editor automatically uses local Compose for the affected Tk 8.6 + IBus/Fcitx combination. If you need an external IME, launch `python3 -B pocket_editor.py --input-method system` or choose **system** under Preferences → Keyboard input and restart. See [keyboard input notes](docs/GUI.md#linux-keyboard-input-and-undo-redo).
