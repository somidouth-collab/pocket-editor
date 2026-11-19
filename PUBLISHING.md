# Publishing Pocket Editor

Author: **Somi Douth** · somidouth@gmail.com

## Build the source release

From the project directory:

```sh
python3 -B tools/documentation/build_docs.py
python3 -B tools/build_release.py
```

The release is written to `dist/Pocket-Editor-<date>.zip`, together with a SHA-256 checksum and release information. Extract it, enter its folder, and run `python3 -B pocket_editor.py`. Tkinter must be installed. On Windows, use `py pocket_editor.py`. Optional PHP dependencies are installed separately with `npm ci --prefix tools/php`; Python formatting optionally uses Black. AI Assistant is included with native Google Gemini and compatible providers; credentials are supplied by each user separately. See docs/AI_ASSISTANT.md.

The archive is a source distribution, not a standalone Windows/macOS/Linux executable. It contains a file manifest and the published author details. Personal settings, sessions/drafts, invitation keys, node_modules, caches, downloads, audit history, and local test projects are excluded. The builder uses an allowlist rather than archiving the whole workspace.

## Public repository or release upload

Upload the checked source archive and its SHA-256 file to the repository/release destination chosen by the owner. A public upload requires the target repository/service and authorized access. A local archive does not itself create a public internet URL. No remote destination is assumed by this project.
