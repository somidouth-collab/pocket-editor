"""PHP formatting, completion, settings, and language-server diagnostics."""
from pocket_editor_plugins import _php_formatter
from pocket_editor_plugins._php_completion_ui import PHPCompletion


def register(api):
    _php_formatter.register(api)
    PHPCompletion(api)
