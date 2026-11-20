# Shared loading animation

The **Loading Indicator** plugin draws an animated blue orbit with a softly pulsing blue center. AI Assistant, Git Project and Codex use it for pending operations. **Plugins → Loading Indicator → Loading animation preview** shows a short demonstration. Multiple operations have independent indicators; finishing one does not stop the others. Unknown-duration operations spin; callers can provide progress between 0 and 1.

## Reuse from another plugin

All service calls and widget updates run on the Tk GUI thread. Run slow work in a worker and deliver its result through a queue; apply the result in your GUI poll callback, then finish the indicator after the surface is ready.

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
            # Apply the result to your widgets before finishing.
            api.call_service('loading_indicator.finish', token)
            token = None

    api.add_command('Start my work', start_work)
    def cancel_work():
        nonlocal token
        if token is not None:
            api.call_service('loading_indicator.stop', token)
            token = None

    api.on_cleanup(cancel_work)
```

`loading_indicator.update(token, message='Indexing…', progress=0.5)` changes the caption/progress. `progress=None` selects the unknown-duration orbit. `finish(token, widget=None, ready=None)` retains the animation through layout and a display frame. For asynchronous rendering, pass a GUI-thread readiness predicate; when supplying a widget, completion also waits for it to be mapped. Other operations keep their own indicators. `stop(token)` cancels immediately, is idempotent and returns whether an active indicator was removed. `status()` returns active tokens and captions.

Without a parent, an unobtrusive card appears at the editor's lower right. Pass `parent=your_empty_frame` to place it in a dedicated container inside a plugin window; the service uses pack within that frame. Destroying the parent removes the indicator on the next animation tick. Closing the editor cancels animation callbacks.

For an optional dependency use `begin(api, message, parent)` and `end(api, token)` from `pocket_editor_plugins._loading`. They tolerate a missing/disabled loading plugin. Call `end` after rendering the success or failure result. Use `cancel(api, token)` during cancellation and cleanup; it removes that operation immediately. If using the service directly, catch `LookupError` when the plugin is unavailable.

Docked panels can set `_loading_ready = False` on the widget returned by their factory while asynchronous setup runs, then set it to `True` on the GUI thread. The sidebar keeps its loading overlay until readiness and layout complete. Switching away and back resumes this overlay without rebuilding a pending panel.
