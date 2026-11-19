import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from pocket_editor_plugins import _git_executable as git


class GitExecutableTests(unittest.TestCase):
    def test_windows_paths_detect_installed_git_without_path_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            expected = base / 'Program Files/Git/cmd/git.exe'
            expected.parent.mkdir(parents=True); expected.touch()
            with patch.object(git.shutil, 'which', return_value=None):
                choices = list(git.candidates(platform='win32', environment={'ProgramFiles': str(base / 'Program Files')}))
            self.assertIn(expected, choices)
            with patch.object(git, 'candidates', return_value=iter(choices)):
                self.assertEqual(git.find_git(), str(expected.resolve()))

    def test_selected_git_is_verified_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            selected = Path(folder) / 'git.exe'; selected.touch()
            with patch.object(git.subprocess, 'run', return_value=Mock(returncode=0, stdout='git version 2.test')) as run:
                path, version = git.verify_git(str(selected))
            self.assertEqual(path, str(selected.resolve()))
            self.assertEqual(run.call_args.args[0], [path, '--version'])
            self.assertNotIn('shell', run.call_args.kwargs)
            with self.assertRaisesRegex(RuntimeError, 'selected Git executable'):
                git.find_git(str(Path(folder) / 'missing.exe'))
            with patch.object(git.subprocess, 'run', return_value=Mock(returncode=0, stdout='not git')):
                with self.assertRaises(RuntimeError): git.verify_git(str(selected))

    def test_windows_install_is_an_argument_list_and_rechecks_git(self):
        with patch.object(git.sys, 'platform', 'win32'), \
             patch.object(git.shutil, 'which', return_value='winget.exe'), \
             patch.object(git.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
             patch.object(git, 'verify_git', return_value=('git.exe', 'git version test')):
            self.assertEqual(git.install_windows_git()[0], 'git.exe')
        self.assertEqual(run.call_args.args[0][:4], ['winget.exe', 'install', '--id', 'Git.Git'])
        self.assertNotIn('shell', run.call_args.kwargs)
        with patch.object(git.sys, 'platform', 'win32'), patch.object(git.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'official installer'): git.install_windows_git()

    @unittest.skipUnless(git.shutil.which('git'), 'Git is not installed')
    def test_selected_installed_git_can_initialize_a_repository(self):
        from pocket_editor_plugins.git_project import GitRunner
        path, version = git.verify_git()
        self.assertTrue(version.startswith('git version '))
        runner = GitRunner()
        runner.executable = path
        try:
            with tempfile.TemporaryDirectory(prefix='pocket-git-native-') as folder:
                runner.run(folder, ['init'])
                self.assertTrue((Path(folder) / '.git').is_dir())
                output, _ = runner.run(folder, ['rev-parse', '--is-inside-work-tree'])
                self.assertEqual(output.strip(), b'true')
        finally:
            runner.close()
