"""Python formatting and background Python/JSON syntax diagnostics."""
from pocket_editor_plugins import _python_formatter
from pocket_editor_plugins._python_diagnostics import Diagnostics


def register(api):
    _python_formatter.register(api)
    Diagnostics(api)
