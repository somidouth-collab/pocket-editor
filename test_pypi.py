import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "packaging"))
from build_pypi import stage_project
from pocket_editor_app.launcher import prepare_runtime
from pocket_editor_app import __version__


class PyPITests(unittest.TestCase):
    def test_stage_has_required_resources_and_no_private_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = stage_project(Path(temp) / "source")
            payload = stage / "pocket_editor_app/_payload"
            for name in ["pocket_editor.py", "docs/index.html", "pocket_editor_plugins/ai_assistant.py",
                         "tools/php/package-lock.json", "pocket_editor_plugins/team_project.py"]:
                self.assertTrue((payload / name).is_file(), name)
            names = [p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file()]
            self.assertFalse(any(n.endswith((".team-invite", "_settings.json", ".pyc")) for n in names))
            self.assertFalse(any(n.startswith(("test_", "audit/", "testp/")) for n in names))
            self.assertFalse((payload / "pocket_editor_session.json").exists())

    def test_runtime_preserves_settings_and_added_plugins(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = stage_project(Path(temp) / "source")
            package = stage / "pocket_editor_app"
            with patch("pocket_editor_app.launcher.__file__", str(package / "launcher.py")), \
                 patch.dict(os.environ, {"POCKET_EDITOR_HOME": str(Path(temp) / "runtime")}):
                runtime = prepare_runtime()
                self.assertEqual(runtime.name, __version__)
                settings = runtime / "pocket_editor_settings.json"
                settings.write_text('{"font_size": 14}')
                plugin = runtime / "pocket_editor_plugins/custom.py"
                plugin.write_text("# personal plugin")
                self.assertEqual(prepare_runtime(), runtime)
                self.assertEqual(settings.read_text(), '{"font_size": 14}')
                self.assertEqual(plugin.read_text(), "# personal plugin")
                self.assertFalse((package / "_payload/pocket_editor_settings.json").exists())


if __name__ == "__main__":
    unittest.main()
