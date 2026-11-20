"""Optional loading-service adapters. Call only on the Tk GUI thread."""
import tkinter as tk


def begin(api, message='Working…', parent=None):
    try:
        return api.call_service('loading_indicator.start', message=message, parent=parent)
    except LookupError:
        return None


def cancel(api, token):
    """Immediately remove a cancelled operation's indicator."""
    if token is not None:
        try:api.call_service('loading_indicator.stop', token)
        except (LookupError, tk.TclError):pass


def end(api, token, widget=None, ready=None):
    """Finish after the caller applied its result and the GUI can display it."""
    if token is not None:
        try:api.call_service('loading_indicator.finish', token, widget=widget, ready=ready)
        except LookupError:cancel(api, token)
        except tk.TclError:pass
