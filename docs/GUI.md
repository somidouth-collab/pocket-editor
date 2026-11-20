# Reference-style interface and vertical plugin tabs

Pocket Editor uses a near-black palette, blue accents, a compact New/Open/Save header, document tabs, and a slim vertical plugin rail inspired by `new_gui.jpeg`. Plugin controls and dialogs share the same default styling. Document color themes and terminal appearance preferences remain user-selectable.

## Choose your own vertical tabs

Click the gear at the bottom of the left rail, or use **Settings → Vertical plugin tabs…**. Check the loaded plugins you want and choose **Save tabs**. Your choices persist in `pocket_editor_settings.json`; hiding a tab does not disable or uninstall its plugin. Use Plugin Manager for startup enable/disable choices.

Click a vertical tab to show its panel. Click it again, or the panel's ×, to collapse it and give the editor more space. Drag the divider to resize. Hover or keyboard-focus a tab to see its full name; Tab and Enter/Space also work. The rail scrolls with the mouse wheel when many tabs are selected.

Project Explorer, AI Assistant, Codex, Git, Terminal, and Team Project support docked panels. Switching tabs preserves each panel's input and running work. Existing standalone windows remain usable; selecting their tab offers their commands rather than duplicating a running tool. Other plugins show an action list, and actions run only when explicitly clicked. Simply selecting Python Tools or a text-transform plugin does not change your document.

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
