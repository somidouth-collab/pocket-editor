# Reference-style interface and vertical plugin tabs

Pocket Editor uses a near-black palette, blue accents, a compact New/Open/Save header, document tabs, and a slim vertical plugin rail inspired by `new_gui.jpeg`. Plugin controls and dialogs share the same default styling. Document color themes and terminal appearance preferences remain user-selectable.

## Choose your own vertical tabs

Click the gear at the bottom of the left rail, or use **Settings → Vertical plugin tabs…**. Check the loaded plugins you want and choose **Save tabs**. Your choices persist in `pocket_editor_settings.json`; hiding a tab does not disable or uninstall its plugin. Use Plugin Manager for startup enable/disable choices.

Click a vertical tab to show its panel. Click it again, or the panel's ×, to collapse it and give the editor more space. Drag the divider to resize. Hover or keyboard-focus a tab to see its full name; Tab and Enter/Space also work. The rail scrolls with the mouse wheel when many tabs are selected.

Project Explorer, AI Assistant, Codex, Git, Terminal, and Team Project support docked panels. Switching tabs preserves each panel's input and running work. Existing standalone windows remain usable; selecting their tab offers their commands rather than duplicating a running tool. Other plugins show an action list, and actions run only when explicitly clicked. Simply selecting Python Tools or a text-transform plugin does not change your document.

The ordinary Plugins menu and optional horizontal toolbar remain available. The toolbar row appears only when commands are pinned there. Plugin and document tabs have separate purposes: document tabs select files, and vertical tabs select tools.

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
