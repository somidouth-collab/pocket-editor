# Reference-style interface and vertical plugin tabs

Pocket Editor uses a near-black palette, blue accents, a compact New/Open/Save header, document tabs, and a slim vertical plugin rail inspired by `new_gui.jpeg`. Plugin controls and dialogs share the same default styling. Document color themes and terminal appearance preferences remain user-selectable.

## Choose your own vertical tabs

Click the gear at the bottom of the left rail, or use **Settings → Vertical plugin tabs…**. Check the loaded plugins you want and choose **Save tabs**. Your choices persist in `pocket_editor_settings.json`; hiding a tab does not disable or uninstall its plugin. Use Plugin Manager for startup enable/disable choices.

Click a vertical tab to show its panel. Click it again, or the panel's ×, to collapse it and give the editor more space. Drag the divider to resize. Hover or keyboard-focus a tab to see its full name; Tab and Enter/Space also work. The rail scrolls with the mouse wheel when many tabs are selected.

Project Explorer, Web Tools, Search & Replace, AI Assistant, Codex, Git, Terminal, and Team Project support docked panels. Switching tabs preserves each panel's input and running work. Existing standalone windows remain usable; selecting their tab offers their commands rather than duplicating a running tool. Other plugins show an action list, and actions run only when explicitly clicked. Simply selecting Python Tools or a text-transform plugin does not change your document.

The menu bar’s Plugins button opens a fixed-size, themed window with vertical scrolling. Select a plugin to see its actions, or right-click a vertical tab. Escape closes the window. The horizontal plugin toolbar has been removed. Plugin and document tabs have separate purposes: document tabs select files, and vertical tabs select tools.

## Panel API for plugin authors

```python
from tkinter import ttk

def register(api):
    def build_panel(parent):
        body = ttk.Frame(parent, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='My tool').pack(anchor='w')
        ttk.Button(body, text='Run', command=run).pack(anchor='w')
        return body

    def run():
        # Explicit user action; perform the actual work here.
        pass

    api.add_panel(build_panel, width=360)
    api.add_command('My tool', run)
```

A plugin has one primary panel. Factories run on the GUI thread and create widgets inside the supplied parent. Width is a preference from 180 to 900 pixels and is clamped to keep editor space available. Hide/show does not dispose the panel, so register worker and widget cleanup through `api.on_cleanup`. Background work must still use a worker/queue rather than blocking Tk.

The bundled `_docking.create_window(root, parent)` adapter supports the main existing plugin surfaces while retaining normal Toplevel windows for their settings dialogs. New plugins should build a normal frame factory directly. If a plugin destroys its docked surface, its factory is called again when the empty panel is reopened.

## Explorer toolbar and context menus

Explorer has one row of small vector buttons: Open Project, Save Project, Load Project, New File, New Folder, Refresh, and More. Hover or keyboard-focus an icon for its name; Enter/Space activates it.

Right-click selects the exact item under the pointer before opening its menu. Files and folders support Open and Copy Path; subitems can be renamed/deleted, while the project root cannot. Right-clicking empty space clears the old selection and disables item actions. Shift+F10 or the Menu key opens the menu for the focused item. Folder Open expands/collapses the tree. New items are created in the selected folder or beside the selected file.

## Startup and closing

The editor window paints before plugin registration. The shared animation shows incremental loading progress; registration stays on the GUI thread and yields between plugins. Session restoration waits for plugin services.

Normal close stores all drafts and workspace state without overwriting project files. The next launch restores clean-close drafts automatically; interrupted sessions still offer recovery. Explorer reopens the last available project, including expanded folders and selected item. Its private `project_explorer_settings.json` is separate from portable project manifests. A failed workspace write keeps the editor open to protect the drafts. Use normal launch, not `--no-session`, to test this behavior.

Explorer mouse menus are posted on button release. Pressing/releasing the opening button cannot itself choose Open; tree refreshes wait until the menu closes. Escape or an outside click dismisses the menu normally.

## First-time panel loading and tab settings

On a panel's first open, a themed loading surface is painted before its widget factory runs. Cached panels reopen immediately and retain their state. Rapidly switching away cancels a pending factory; closing the editor also cancels pending UI opens. First calls to standard Show/Settings/Preferences window commands use the same loading service. Tk widgets are still created on the GUI thread; slow work should use workers rather than block a factory.

The vertical-tab picker has a scrollable checklist and a fixed footer. Save tabs and Cancel remain visible when the window is small or many plugins are installed. Mouse wheel, Linux wheel buttons, and keyboard focus scrolling work within the checklist. The picker and loading cards use the editor's near-black surfaces and blue accent.

On first opening a vertical panel, the loading surface is painted before the panel factory starts. Panels are cached after opening; switching back preserves their contents.

## Linux keyboard input and Undo/Redo

Empty Undo/Redo operations are harmless, including requests originating in Tk’s Tcl event loop. They do not mark the document as changed; ordinary undoable edits and redo still work.

On Linux with Tk 8.6 and the IBus/Fcitx XIM bridge, Automatic input mode starts Tk with local Compose input to avoid repeated fabricated-key-event warnings. XIM remains enabled for local compose and normal character input. The editor immediately restores its environment, so project subprocesses inherit the desktop’s original input-method configuration. No system packages or desktop settings are changed.

External IME composition (for example IBus/Fcitx Chinese/Japanese input) needs **system** mode. Select **Settings → Preferences → Keyboard input → system**, apply, and restart. You can also choose it for one launch:

```bash
python3 -B pocket_editor.py --input-method system
```

`--input-method local` explicitly selects local Compose. The default is `auto`. These switches only affect the Linux/X11 keyboard backend and do not alter project files.

References: [Tk’s XIM initialization](https://github.com/tcltk/tk/blob/core-8-6-branch/unix/tkUnixEvent.c) and [Xlib input-context focus fix](https://lists.debian.org/debian-x/2026/02/msg00185.html).

## Windows clipboard and selection safety

The local text-widget proxy forwards native commands through Tcl, preserving the completion/error codes that Tk’s Windows Paste, Copy, Cut, Clear, Undo, and selection bindings expect. Python observes successful edits rather than forwarding every native text command. Pasting with no selection no longer leaks an expected selection error into the Python main loop; native selection replacement and undo grouping are retained. Empty Undo/Redo operations remain harmless.

Edit-menu clipboard commands target the focused text input (including settings/search entries) instead of unexpectedly modifying the document. Split-pane editing still targets the focused document.

The regression suite exercises Tk’s actual Windows Paste procedure branch on the development host and includes keyboard events, missing/non-text clipboard handling, selection replacement, Unicode, undo/redo, and widget cleanup. This is not a claim of a completed Windows 10 desktop test. On Windows with Python/Tk installed, run:

```bat
py -B -m unittest -v test_windows_editing test_keyboard_input
```

Windows CI is configured locally to include these tests when the workflow is next published/run. No workflow or release is uploaded as part of this local fix.

The local Python formatter also suppresses a separate console window for its Black subprocess on Windows. Native Windows 10 desktop validation is still needed; current checks execute Tk’s Windows paste branch on the Linux development host and cover keyboard/clipboard behavior without claiming to simulate the Windows clipboard driver.

For a Windows source checkout or the local source test archive, run:

```powershell
py -B -m unittest -v test_windows_editing test_keyboard_input
py pocket_editor.py
```

The local publishing workflow includes these GUI checks in its Windows job for the next authorized publication. No workflow has been uploaded or run remotely for these local changes.

## Document keyboard shortcuts

Use Ctrl+C to Copy, Ctrl+X to Cut, Ctrl+V to Paste, Ctrl+A to Select All, Ctrl+Z to Undo, and Ctrl+Y or Ctrl+Shift+Z to Redo. On macOS use Command instead of Ctrl. These shortcuts act on the focused document, including newly opened tabs and split panes. The Edit menu displays the primary shortcuts. Empty Undo/Redo is harmless.

Additional shortcuts (Ctrl becomes Command on macOS):

| Action | Shortcut |
| --- | --- |
| New / Open / Save | Ctrl+N / Ctrl+O / Ctrl+S |
| Save As / Save All | Ctrl+Shift+S / Ctrl+Shift+A |
| Close document / Exit | Ctrl+W / Ctrl+Q |
| Quick Open / Command Palette | Ctrl+P / Ctrl+Shift+P |
| Find / Find Next | Ctrl+F / Ctrl+G or F3 |
| Search & Replace | Ctrl+H (Search & Replace plugin) |
| Go to Line | Ctrl+L (Editor Commands plugin) |
| Next / Previous tab | Ctrl+Tab / Ctrl+Shift+Tab |
| Next / Previous tab alternative | Ctrl+PageDown / Ctrl+PageUp |
| Duplicate line or selected lines | Ctrl+D |
| Move lines up / down | Alt+Up / Alt+Down |
| Delete lines | Ctrl+Shift+K |
| Close other tabs | Ctrl+Alt+W |
| Zoom in / out / reset | Ctrl++ or Ctrl+= / Ctrl+- / Ctrl+0 |
| Help | F1 |

Tab navigation, line operations, and zoom shortcuts require the Editor Commands plugin. Standard arrow, Home/End, Ctrl+Home/End, Shift selection, and Ctrl+Left/Right word navigation use native text bindings. Document shortcuts apply while a document has keyboard focus; input fields keep their own native editing behavior.

## Search & Replace: vertical tab or separate window

Ctrl+F and Ctrl+H (Command on macOS) open the Search & Replace plugin in its vertical tab. The Edit menu Find and Replace actions use this same plugin. Ctrl+G or F3 finds the next match; Ctrl+Shift+G or Shift+F3 finds the previous match. If the plugin is disabled, enable it in Plugin Manager. The old separate core Find dialog is no longer used.

The vertical panel contains both inputs, regular-expression and case options, Previous/Next, Replace, Replace All, status, and window opacity. Resize its divider or use its vertical scrollbar to reach all controls. Enter finds the next match and Shift+Enter the previous match while an input is focused. Searches wrap at the document boundaries.

Click **Open separate window**, or choose **Search & Replace — Separate Window** from the plugin menu, to use a floating search GUI. **Use vertical tab** closes that window and returns to the dock. Both views share the same search text, replacement, options, and result status. Replacements target the active document and can be undone.

**Window opacity** ranges from 40% to 100%; 100% is solid. Changes affect only the separate Search & Replace window, never the editor or dock. Release the slider or use its keyboard controls to save the value in `search_replace_settings.json`. Window managers without alpha support may ignore transparency or report it unavailable. No search text or document content is saved in that settings file.

## Document right-click menu

Right-click inside a document for Undo/Redo, Cut, Copy, Paste, Delete Selection, Select All, Find/Replace, Save, Copy File Path, and Close File. When Editor Commands is enabled, the same menu includes Duplicate Line/Selection, Delete Lines, and Move Lines Up/Down. Find/Replace opens the Search & Replace plugin.

Right-click inside a selection preserves it. Clicking elsewhere moves the caret and clears the selection without editing the text. Split panes use the document you actually clicked. Actions that need a selection, undo history, a saved path, or an enabled plugin are disabled when unavailable. Closing a file retains the normal unsaved-change prompt.

Shift+F10 or the Menu key opens the same menu at the caret without changing the selection. macOS also supports Control-click. Escape dismisses the menu. Existing Project Explorer item menus remain available.

## Smart external-file monitoring

Pocket Editor checks open saved files in an isolated helper process about every two seconds. It waits briefly for a changed file to settle and verifies its content hash, so timestamp-only touches and the editor's own Save do not trigger reload notices. It also detects atomic replacement saves. Small files get an occasional content recheck even if metadata is unchanged.

Clean UTF-8 documents reload automatically, preserving the cursor and scroll position where possible, UTF-8 BOM and line endings. A banner and ↻ tab marker show which files changed, including inactive tabs; monitoring does not switch focus. The previous text remains available through Undo.

Documents with unsaved text or rich formatting are kept intact. Their banner offers **Reload** (with confirmation before replacing edits) and **Keep Mine** (keeps the draft and acknowledges the disk version). × dismisses the notice. Save independently checks the current disk version and asks before overwriting an unacknowledged external change. The normal explicit Save policy still controls file writes.

Deleted, renamed, unreadable or invalid-UTF-8 files never clear the open text. Missing/unreadable copies are retained as drafts. Files larger than 8 MB show a notice and require explicit Reload rather than automatic replacement; their hashes are checked in chunks when metadata changes. Recovered drafts retain their last known disk baseline when available. Open menus and modal dialogs defer automatic refreshes. Closing a document or the editor cancels its pending reload updates and terminates the owned checker process.
