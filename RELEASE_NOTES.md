# Pocket Editor — 2026.11.22

Created by **Somi Douth** · somidouth@gmail.com

- Fixed Windows clipboard Paste with and without a selection, and empty Undo/Redo. Native Tk errors stay within Tcl event handling.
- Fixed Copy, Cut, and Clear with no selection and Edit-menu actions targeting the wrong focused text field.
- Suppressed Windows console flashes during Python formatting.
- Added FusionPHP Tools for .fpf templates: theme-aware syntax colors, formatting, autocomplete, syntax checking, and red zigzag error marks. Includes offline examples and Help.
- Added Linux Tk input-method compatibility settings to avoid XIM fabricated-key warnings while preserving a system input-method option.
- Expanded clipboard, undo, focus, and keyboard regression coverage and Windows CI checks.

Validation: 73 local tests passed. Native Windows desktop CI is required before PyPI publication; Windows 10 manual verification remains outstanding. FusionPHP checks use the local framework grammar. No external AI generation requests were made.

Upgrade with `pipx upgrade pocket-editor`, then restart. Runtime folders are version-specific; existing drafts and settings remain in the previous version folder. License remains undecided.

---

# Pocket Editor — 2026.11.20

Created by **Somi Douth** · somidouth@gmail.com

- Added **C/C++ Tools**: clang-format formatting, clangd autocomplete, compiler syntax checks with red zigzag errors and amber warnings, and GDB debugging with breakpoints, stepping, variables, and call stacks.
- Fixed C/C++ Tools so menu commands and vertical tabs reuse the same embedded panel. Reopening and switching tabs preserve its inputs and debugger data.
- Kept loading animations active through result rendering and display readiness. Concurrent operations have independent indicators, and pending panels can report asynchronous readiness.
- Removed the horizontal plugin toolbar. Plugins remain accessible through the menu bar, command palette, and chosen vertical tabs. The Plugins window has a fixed size and vertical scrolling.
- Fixed Project Explorer item context menus, compacted its actions into one icon row, and restored the last project, expanded folders, and selection.
- Preserved unsaved drafts, document state, and split views across normal editor restarts. Explicit Save still controls writes to project files. The editor paints before incremental plugin registration.
- Improved AI Assistant connection repair and Gemini model discovery. Repair refreshes available models without automatically retrying generation or choosing a replacement model.
- Added a persistent SQLite index and SHA-256 content store for Team Project, changed-entry manifest sync, version-based conflict checks, and local conflict recovery copies.
- Added invitation roles (read-only, member, admin), project-folder restrictions, and **Remove Client** to invalidate unwanted client keys across host restarts.
- Updated local Help, installation instructions, examples, and plugin development documentation.

## Requirements and scope

C/C++ features require external tools. On Debian: `sudo apt install clang-format clangd gdb build-essential`. Single-file debug builds are supported; build multi-file projects with their existing build system and select the resulting executable. LLDB, remote debugging, and interactive debug-program input are not implemented.

Team sync remains explicit and host-authoritative. Resumable transfers, push notifications, per-client quotas, automatic backups, and a history-restore GUI are not included in this release. Legacy clients retain hash-based checks; update both ends for version-based checks.

Linux GUI checks and real clang-format, clangd, compiler, and GDB tests passed. Windows compatibility checks cover selected protocol and executable-discovery code; Windows/macOS desktop behavior remains unverified. AI tests use offline transports and do not guarantee provider/account availability.

The source archive and PyPI package exclude private settings, session drafts, invitation files, credentials, caches, and local tool downloads. Versioned PyPI runtime folders remain separate; restart after upgrading. License remains undecided.
