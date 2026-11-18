# Pocket Editor plugin development

## Start with Plugin Builder

1. Open Extensions → Plugin Builder… (restart the editor or choose Extensions → Load New Plugins if the builder is newly installed).
2. Enter a unique plugin ID such as `my_tools`, a command label, and an optional description.
3. Choose Simple command, Text transform, Tool window, or Events and services.
4. Review Code preview. Previewing validates and compiles the generated source without running it.
5. Click Create & load. The builder creates `pocket_editor_plugins/<plugin_id>.py`, loads it, and opens its source in a file tab. Existing files are never overwritten.
6. Run your command from Extensions. Add a shortcut button through Extensions → Add to toolbar.
7. Edit the source and save it. Restart the editor to apply changes to a plugin already loaded. Load New Plugins loads only files not already loaded; it does not reload edited plugins.

The builder needs only Python and Tkinter. Templates are starting points, not a package installer or an AI code generator.

## Minimal plugin

```python
from tkinter import messagebox


def register(api):
    def run():
        messagebox.showinfo("My command", f"{len(api.get_text()):,} characters")

    api.add_command("My command", run)
```

Place this file directly in `pocket_editor_plugins` beside `pocket_editor.py`. Files beginning with `_` are helpers and are not loaded as plugins. The filename stem is the plugin ID. Use a distinct command label so users can recognize your plugin.

The loader executes each Python file, then calls `register(api)`. Commands are installed after registration succeeds. Keep imports and registration quick; do expensive work when a command runs. A plugin runs with the editor's permissions: install code you trust. `--safe-mode` starts without plugins.

## Public PluginAPI reference

| API | Behavior |
| --- | --- |
| `api.plugin_id` | Plugin ID, derived from the filename stem. |
| `api.add_command(label, callback)` | Register a nonempty menu label and a callable taking no arguments. Call during `register`. |
| `api.get_text()` | Return the entire active tab as a string, excluding Tk's automatic trailing newline. |
| `api.replace_text(text)` | Replace the active tab's text as one undo group. Marks it modified; does not save to disk. Requires a string. |
| `api.get_path()` | Return the active tab's `pathlib.Path`, or `None` for an untitled tab. |
| `api.list_plugins()` | Return sorted loaded plugin IDs as a tuple. |
| `api.on(event, callback)` | Subscribe to an event; returns a callable that unsubscribes. Callback receives keyword arguments including `source`. |
| `api.emit(event, **payload)` | Queue an event for idle delivery to loaded plugins. The editor supplies `source=api.plugin_id`; do not put `source` in payload. |
| `api.provide_service(name, callback)` | Register a synchronous service named `plugin_id.name`. Names must be nonempty and contain no dot. Duplicate names in a plugin are rejected. |
| `api.list_services()` | Return sorted qualified service names as a tuple. |
| `api.call_service("plugin_id.name", *args, **kwargs)` | Call a loaded service and return its result. Raises `LookupError` when unavailable. |
| `api.on_cleanup(callback)` | Register a no-argument cleanup callback, run in reverse order on editor exit or failed plugin registration. |

These APIs and Tk operations must be used on the GUI thread. `list_plugins()` and `list_services()` reflect plugins that have finished loading; dependencies may not yet be available during registration. Resolve optional services when commands run and catch `LookupError`.

## Events

```python
def register(api):
    def saved(source, path, **payload):
        print("Saved", path, "from", source)

    api.on("document_saved", saved)
```

| Event | Source / payload |
| --- | --- |
| `document_changed` | Core source `editor`, no required extra fields; queued and coalesced for edits and tab switches. Some plugins also emit this event. |
| `document_switched` | Core source `editor`, no extra fields; delivered when selecting another tab. |
| `document_saved` | Core source `editor`, `path` string. Delivery is queued; another tab may be active by delivery time. |
| `plugin_loaded` | Loaded plugin's ID as source, `plugin_id` string. |
| `document_formatted` | Formatter plugin source; payload varies by formatter. |
| `project_changed` | Project plugin source; payload varies, including file actions or project directory. |

Use callbacks accepting `**payload` for compatibility with extra fields. Namespaced custom events such as `my_tools.completed` avoid collisions. Do not emit the same event unconditionally from its handler: that creates an endless feedback loop. Subscription cleanup is automatic; use the unsubscribe callable when you want to stop earlier.

## Services

```python
def register(api):
    api.provide_service("word_count", lambda: len(api.get_text().split()))

    def show_count():
        try:
            count = api.call_service("other_plugin.word_count")
        except LookupError:
            return  # Optional dependency is not loaded.
        print(count)

    api.add_command("Use another plugin", show_count)
```

Service calls are synchronous. Keep them short and handle failures at the caller. There is no manifest, automatic dependency installation, or dependency-order resolver; plugin files load in filename order.

## Two plugins communicating

The builder's Plugin communication tab lists loaded plugins and their available services. Refresh services after loading another plugin. The Events and services template publishes `<plugin_id>.word_count`, sends `<plugin_id>.counted` when its command runs, and forwards saves as `<plugin_id>.observed_save`.

For a complete example, create an Events and services plugin with ID `counter`. Then create `counter_view.py` with this code:

```python
from tkinter import messagebox


def register(api):
    def counted(source, count, **payload):
        # Called asynchronously after counter emits its counted event.
        print(f"{source} counted {count} words")

    api.on("counter.counted", counted)

    def show_count():
        # Service calls return a result immediately on the GUI thread.
        try:
            count = api.call_service("counter.word_count")
        except LookupError:
            messagebox.showinfo("Counter", "Load the counter plugin first.")
            return
        messagebox.showinfo("Counter", f"{count:,} words in the active tab")

    api.add_command("Show shared word count", show_count)
```

Choose Extensions → Load New Plugins. Run counter's command to send an event to `counter_view`; run Show shared word count to request counter's service. Both use the active file tab. Events carry notifications; services return requested values. Plugins need no direct Python imports of one another. Calls belong in command callbacks rather than module-level code, because the other plugin may still be loading.

## GUI tools and cleanup

The Tool window template uses `api._editor.root`, an internal integration interface used by bundled plugins. This is distinct from the public API above and may change. Prefer `ttk` widgets to inherit the editor theme. Keep one window instance, raise it when the command is repeated, and register a cleanup callback to destroy it.

The editor's `api._editor.buffer` is a stable interface forwarding to the selected tab. Do not cache a tab's text, path, selection, or cursor and later assume that tab is still active. Read them when a command runs. Each tab keeps its own text, tags, undo history, and cursor. Plain-text saving does not preserve rich-text styles; use a formatted copy for those.

For slow work, capture inputs on the GUI thread, use a worker for computation or I/O, and return results through a queue polled with `root.after` from the GUI thread. Workers must not call Tk or PluginAPI methods. Before applying a result, verify the captured document generation and revision against `api._editor.document_generation` and `api._editor.document_revision`; discard stale results after edits or tab switches. These counters are internal integration details. Stop workers, cancel scheduled callbacks, close windows, and remove custom bindings in `on_cleanup`. Do not block shutdown indefinitely.

## Test and troubleshoot

- Test a generated transform with `transform("Hello")` before wiring up more UI.
- Use a scratch file. Run the command, switch tabs, and check Undo, unsaved markers, and Save.
- For windows, repeat the command and close/reopen the window; verify editor shutdown.
- For services, check `list_services()` and handle missing dependencies.
- A syntax/import/registration error appears in Plugin loading errors. Fix the file and restart, or use Load New Plugins if that file never loaded successfully.
- A command exception appears in Plugin error. Registration failures roll back registered API commands/services/subscriptions and invoke registered cleanup callbacks; external side effects require your own cleanup.
- If startup fails because of custom code, launch `python3 -B pocket_editor.py --safe-mode`, fix the plugin, and restart normally.

Run the builder tests from the project directory on a graphical desktop:

```sh
python3 -B -m unittest -v test_plugin_builder
```

## Inspect with Plugin Manager

Settings → Plugin Manager lists installed plugins and shows registered commands, subscribed events, callable services, and recorded registration/command errors. Enable or disable startup loading there. Disable takes effect after restart for already-loaded plugins; Load new plugins can load newly enabled or repaired plugins that have not loaded successfully yet. Safe mode still provides this manager through Settings. Inspect a plugin's services before connecting it to another plugin, and use the builder's Plugin communication tab for a combined service list.

## Optional project tools

`git_project.ensure_repository(folder)` synchronously checks a local folder, initializes Git if missing, registers the actual working-tree root, and returns that root as a Path. It works before the Git window opens and does not clone, stage, commit, or contact a remote. Existing repositories are reused, including when a subfolder is supplied. Invalid existing Git metadata produces an error instead of being replaced.

`git_project.get_repository()` returns the registered root or None. It does not return an uninitialized typed folder. `git_project.get_project()` is a compatibility alias. `git_project.open_project(folder)` prepares/registers the folder and opens the independent Git UI. Services run on the GUI thread; local setup commands use bounded timeouts and setup is refused while another Git operation is running.

```python
root = api.call_service("git_project.ensure_repository", project_folder)
# Other plugins can retrieve the same registered root later:
root = api.call_service("git_project.get_repository")
```

Subscribe to `git_project.repository_changed` to observe registration changes. The payload contains `path` (string) and `initialized` (bool), plus the usual `source`. Resolve optional services when a user invokes a command and handle LookupError if Git Project is unavailable. Handle ValueError, OSError, or RuntimeError for invalid paths, missing Git, and Git failures.

The Git plugin works without Project Explorer or Team Project. Project Explorer Actions → Git Project selects, prepares, and registers its chosen folder. `team_project.open_project(folder)` separately opens hosted project collaboration and refuses to switch an active connection.
