# Pocket Editor — 2026.11.19.post2

Created by **Somi Douth** · somidouth@gmail.com

- Updated the editor and default plugin styling to follow the supplied GUI reference: near-black surfaces, blue accents, a slim activity rail, compact document tabs and a docked Explorer.
- Added user-selected vertical plugin tabs, persisted choices, panel resizing/collapse, hover labels and keyboard activation. The horizontal shortcut toolbar is retained and hidden when empty.
- Added state-preserving docked panels for Explorer, AI Assistant, Codex, Git, Terminal and Team Project. Other plugins expose explicit action lists without running transformations when a tab is selected.
- Added `PluginAPI.add_panel(factory, width=360)` and documented panel lifecycle for third-party plugins. Docked tools retain their normal settings dialogs and existing standalone-window behavior.
- Removed redundant single-pane file headings, refreshed the preview images and updated offline Help.

The layout is implemented with Tk/ttk and built-in vector icons; it has no additional application dependency. Native title bars and menus follow the operating system. The reference image is not required at runtime. Versioned PyPI runtime folders remain separate; restart after upgrading. License remains undecided.
