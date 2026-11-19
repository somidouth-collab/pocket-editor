# Shared loading animation

The **Loading Indicator** plugin draws an animated blue/violet orbit with a softly pulsing center. AI Assistant, Git Project and Codex use it for pending operations. **Extensions → Loading animation preview** shows a short demonstration. Multiple operations have independent indicators; finishing one does not stop the others. Unknown-duration operations spin; callers can provide progress between 0 and 1.

## Reuse from another plugin

All service calls and widget updates run on the Tk GUI thread. Run slow work in a worker and deliver its result through a queue; stop the indicator from your GUI poll callback.

```python
def register(api):
    token = None

    def start_work():
        nonlocal token
        token = api.call_service('loading_indicator.start', message='Loading project…')
        # Start your background worker here; do not call Tk/services in it.

    def finish_work():
        nonlocal token
        if token is not None:
            api.call_service('loading_indicator.stop', token)
            token = None

    api.add_command('Start my work', start_work)
    api.on_cleanup(finish_work)
```

`loading_indicator.update(token, message='Indexing…', progress=0.5)` changes the caption/progress. `progress=None` selects the unknown-duration orbit. `stop(token)` is idempotent and returns whether an active indicator was removed. `status()` returns active tokens and captions.

Without a parent, an unobtrusive card appears at the editor's lower right. Pass `parent=your_empty_frame` to place it in a dedicated container inside a plugin window; the service uses pack within that frame. Destroying the parent removes the indicator on the next animation tick. Closing the editor cancels animation callbacks.

For an optional dependency use `begin(api, message, parent)` and `end(api, token)` from `pocket_editor_plugins._loading`. They tolerate a missing/disabled loading plugin. Always stop your token on both success and failure and during cleanup. If using the service directly, catch `LookupError` when the plugin is unavailable.
