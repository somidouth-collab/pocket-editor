# Install Pocket Editor

Created by Somi Douth · somidouth@gmail.com

Pocket Editor is a Python source release. It requires Python 3.9 or newer and Tkinter.

## Debian and Ubuntu

Install the core requirements:

```sh
sudo apt update
sudo apt install python3 python3-tk unzip
```

Download `Pocket-Editor-2026.11.18.zip` from the release page, then run:

```sh
unzip Pocket-Editor-2026.11.18.zip
cd Pocket-Editor-2026.11.18
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

## Optional features

The core editor requires no pip packages. PHP tools require Node.js/npm and `npm ci --prefix tools/php --ignore-scripts`. Python formatting optionally uses Black. AI services require each user's own provider configuration. Team internet sharing offers relay installation in its GUI.

## Help and support

- Source and documentation: https://github.com/somidouth-collab/pocket-editor
- Downloads: https://github.com/somidouth-collab/pocket-editor/releases
- Report a bug or request a feature: https://github.com/somidouth-collab/pocket-editor/issues
- SourceForge project: https://sourceforge.net/projects/pocket-editor/

For bug reports, include your operating system, Python version, reproduction steps, and error message. Remove credentials and invitation keys before sharing logs.
