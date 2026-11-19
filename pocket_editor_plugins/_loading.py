"""Optional loading-service adapters. Call only on the Tk GUI thread."""
import tkinter as tk


def begin(api, message='Working…', parent=None):
    try:
        return api.call_service('loading_indicator.start', message=message, parent=parent)
    except LookupError:
        return None


def end(api, token):
    if token is not None:
        try:
            api.call_service('loading_indicator.stop', token)
        except (LookupError, tk.TclError):
            pass
