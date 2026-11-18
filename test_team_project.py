"""Local Git integration tests; no network service or credentials required."""
import gc
import os
from pathlib import Path
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import pocket_editor as core
from pocket_editor_plugins.git_project import GitRunner, GitProject, changed_paths


class GitTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix="pocket-team-test-")
        self.base = Path(self.temp.name)
        self.runner = GitRunner()
        self.origin = self.base / "origin.git"
        self.git(self.base, "init", "--bare", str(self.origin))
        self.first = self.base / "first"
        self.git(self.base, "clone", str(self.origin), str(self.first))
        self.identity(self.first)
        (self.first / "hello.txt").write_text("initial\n")
        self.git(self.first, "add", "hello.txt")
        self.git(self.first, "commit", "-m", "Initial")
        self.git(self.first, "push", "-u", "origin", "HEAD")

    def tearDown(self):
        self.runner.close()
        self.temp.cleanup()

    def git(self, folder, *args):
        environment = os.environ.copy()
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.check_output(["git", "-C", str(folder), *args],
                                       stderr=subprocess.STDOUT, env=environment)

    def identity(self, folder):
        self.git(folder, "config", "user.name", "Test")
        self.git(folder, "config", "user.email", "test@example.invalid")
        self.git(folder, "config", "commit.gpgsign", "false")

    def test_two_clones_exchange_commits_and_reject_divergence(self):
        second = self.base / "second"
        self.runner.run(self.base, ["clone", "--", str(self.origin), str(second)])
        self.identity(second)
        (self.first / "hello.txt").write_text("from teammate\n")
        self.git(self.first, "commit", "-am", "Teammate edit")
        self.runner.run(self.first, ["push"])
        self.runner.run(second, ["pull", "--ff-only", "--no-rebase"])
        self.assertEqual((second / "hello.txt").read_text(), "from teammate\n")
        (second / "local.txt").write_text("local")
        self.git(second, "add", "local.txt")
        self.git(second, "commit", "-m", "Local")
        (self.first / "remote.txt").write_text("remote")
        self.git(self.first, "add", "remote.txt")
        self.git(self.first, "commit", "-m", "Remote")
        self.runner.run(self.first, ["push"])
        before = self.git(second, "rev-parse", "HEAD")
        with self.assertRaises(RuntimeError):
            self.runner.run(second, ["pull", "--ff-only", "--no-rebase"])
        self.assertEqual(self.git(second, "rev-parse", "HEAD"), before)

    def test_status_handles_rename_unicode_and_literal_names(self):
        self.git(self.first, "mv", "hello.txt", "renamed file.txt")
        (self.first / "[a] café.txt").write_text("new")
        output, _ = self.runner.run(self.first, ["status", "--porcelain=v1", "-z"])
        rows = changed_paths(output)
        self.assertIn(("R ", "renamed file.txt", ["renamed file.txt", "hello.txt"]), rows)
        self.assertIn(("??", "[a] café.txt", ["[a] café.txt"]), rows)
        with self.assertRaises(ValueError):
            changed_paths(b"R  new\0")

    def test_closed_runner_and_git_failure(self):
        with self.assertRaises(RuntimeError):
            self.runner.run(self.base, ["status"])
        self.runner.close()
        with self.assertRaises(RuntimeError):
            self.runner.run(self.first, ["status"])


class TeamGuiTests(GitTests):
    # Reuse Git setup without running the parent's tests twice.
    test_two_clones_exchange_commits_and_reject_divergence = None
    test_status_handles_rename_unicode_and_literal_names = None
    test_closed_runner_and_git_failure = None

    def setUp(self):
        super().setUp()
        self.root = tk.Tk()
        self.root.withdraw()
        self.settings = patch.object(core, "SETTINGS_PATH", self.base / "settings.json")
        self.settings.start()
        self.editor = core.Editor(self.root, True)
        self.api = core.PluginAPI(self.editor, "git_project")
        self.panel = GitProject(self.api)
        self.panel.show()
        self.panel.folder.set(str(self.first))
        self.panel.refresh()
        self.wait_idle()

    def tearDown(self):
        try:
            self.panel.cleanup()
            for document in self.editor.documents:
                document["widget"].edit_modified(False)
                document["rich_dirty"] = False
            self.editor.rich_dirty = False
            self.editor.quit()
        finally:
            self.settings.stop()
            super().tearDown()

    def wait_idle(self):
        end = time.monotonic() + 10
        while self.panel.busy and time.monotonic() < end:
            self.root.update()
            time.sleep(.01)
        self.root.update()
        self.assertFalse(self.panel.busy)

    def test_commit_only_selected_files_and_refresh(self):
        (self.first / "[a].txt").write_text("selected")
        (self.first / "other.txt").write_text("not selected")
        self.git(self.first, "add", "other.txt")
        self.panel.refresh(); self.wait_idle()
        item = next(item for item, paths in self.panel.rows.items() if paths == ["[a].txt"])
        self.panel.tree.selection_set(item)
        self.panel.message.set("Selected change")
        self.panel.commit(); self.wait_idle()
        files = self.git(self.first, "show", "--pretty=", "--name-only", "HEAD").decode().splitlines()
        self.assertEqual(files, ["[a].txt"])
        staged = self.git(self.first, "diff", "--cached", "--name-only").decode().splitlines()
        self.assertEqual(staged, ["other.txt"])
        self.assertEqual(self.panel.message.get(), "")

    def test_pull_blocks_open_tabs_and_dirty_worktree(self):
        self.editor.open_path(self.first / "hello.txt")
        with patch("pocket_editor_plugins.git_project.messagebox.showerror") as error:
            self.panel.pull()
            error.assert_called_once()
        self.assertFalse(self.panel.busy)
        self.editor.close_document(self.editor.active_document)
        (self.first / "hello.txt").write_text("local change")
        self.panel.pull(); self.wait_idle()
        self.assertIn("failed", self.panel.status.get())
        self.assertEqual((self.first / "hello.txt").read_text(), "local change")

    def test_no_selection_and_folder_change_invalidate_review(self):
        with patch("pocket_editor_plugins.git_project.messagebox.showerror") as error:
            self.panel.commit()
            error.assert_called_once()
        self.panel.folder.set(str(self.base / "missing"))
        self.assertIsNone(self.panel.reviewed)
        self.assertEqual(self.panel.rows, {})
        with patch("pocket_editor_plugins.git_project.messagebox.showerror") as error:
            self.panel.refresh()
            error.assert_called_once()

    def test_clone_opens_in_project_explorer(self):
        provider = core.PluginAPI(self.editor, "project_explorer")
        opened = []
        provider.provide_service("open_project", lambda path: opened.append(path))
        self.editor.plugin_apis["project_explorer"] = provider
        self.panel.remote.set(str(self.origin))
        with patch("pocket_editor_plugins.git_project.filedialog.askdirectory", return_value=str(self.base)), patch("tkinter.simpledialog.askstring", return_value="team-clone"):
            self.panel.clone()
            self.wait_idle()
        clone = self.base / "team-clone"
        self.assertEqual(opened, [clone])
        self.assertEqual(self.panel.reviewed, clone)
        self.assertEqual((clone / "hello.txt").read_text(), "initial\n")

    def test_folder_selection_initializes_and_exposes_repository_to_other_plugins(self):
        folder = self.base / 'automatically-prepared'
        folder.mkdir()
        (folder / 'keep.txt').write_text('existing application data')
        self.editor.plugin_apis['git_project'] = self.api
        consumer = core.PluginAPI(self.editor, 'other_plugin')
        repository = consumer.call_service('git_project.ensure_repository', folder)
        self.assertEqual(repository, folder)
        self.assertTrue((folder / '.git').is_dir())
        self.assertEqual(consumer.call_service('git_project.get_repository'), folder)
        self.assertEqual(consumer.call_service('git_project.get_project'), folder)
        self.assertEqual((folder / 'keep.txt').read_text(), 'existing application data')
        self.assertEqual(consumer.call_service('git_project.ensure_repository', folder), folder)
        self.assertEqual(self.git(folder, 'status', '--porcelain').decode().strip(), '?? keep.txt')

    def test_nested_folder_uses_existing_repository_root_without_nested_init(self):
        nested = self.first / 'src'; nested.mkdir()
        root = self.panel.ensure_repository(nested)
        self.assertEqual(root, self.first)
        self.assertFalse((nested / '.git').exists())
        self.assertEqual(self.panel.get_repository(), self.first)

    def test_service_is_available_before_git_window_opens(self):
        api = core.PluginAPI(self.editor, 'independent_git')
        panel = GitProject(api)
        folder = self.base / 'no-window'; folder.mkdir()
        try:
            self.assertIsNone(panel.get_repository())
            self.assertEqual(panel.ensure_repository(folder), folder)
            self.assertIsNone(panel.window)
            self.assertEqual(panel.get_repository(), folder)
        finally:
            panel.cleanup()

    def test_service_opens_arbitrary_project_without_team_plugin(self):
        folder = self.base / 'independent-project'
        folder.mkdir()
        self.panel.open_project(folder)
        self.assertEqual(self.panel.folder.get(), str(folder))
        self.assertFalse(hasattr(self.panel, 'chat'))
        self.panel.init_repository()
        self.wait_idle()
        self.assertTrue((folder / '.git').is_dir())
        self.assertEqual(self.panel.reviewed, folder)

    def test_set_origin_and_first_push_configure_upstream(self):
        folder = self.base / 'new-repository'
        folder.mkdir()
        self.git(folder, 'init')
        self.identity(folder)
        (folder / 'first.txt').write_text('first project file')
        self.git(folder, 'add', 'first.txt')
        self.git(folder, 'commit', '-m', 'First commit')
        remote = self.base / 'new-remote.git'
        self.git(self.base, 'init', '--bare', str(remote))
        self.panel.open_project(folder); self.wait_idle()
        self.panel.remote.set(str(remote))
        self.panel.set_origin(); self.wait_idle()
        self.panel.push(); self.wait_idle()
        self.assertNotIn('failed', self.panel.status.get())
        self.assertTrue(self.git(folder, 'rev-parse', '--abbrev-ref', '@{u}').decode().startswith('origin/'))

    def test_window_reopens_and_cleans_up(self):
        window = self.panel.window
        self.panel.window.withdraw()
        self.panel.show()
        self.assertIs(self.panel.window, window)
        self.panel.cleanup()
        self.assertIsNone(self.panel.job)
        self.assertFalse(window.winfo_exists())
