"""Real Git config saves, inherited values, lock safety, and settings GUI."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pocket_editor_plugins._git_settings import FIELDS, normalize, read_settings, save_settings
import test_team_project as fixtures


class SettingsTests(fixtures.GitTests):
    test_closed_runner_and_git_failure = None
    test_status_handles_rename_unicode_and_literal_names = None
    test_two_clones_exchange_commits_and_reject_divergence = None

    def test_round_trip_applies_identity_and_preserves_other_git_settings(self):
        self.git(self.first, 'config', 'custom.keep', 'untouched')
        settings = read_settings(self.runner, self.first)['local']
        settings.update({'user.name': 'New Developer', 'user.email': 'new@example.invalid',
                         'core.autocrlf': 'input', 'core.filemode': 'false', 'commit.gpgsign': 'false',
                         'remote.origin.url': str(self.origin)})
        result = save_settings(self.runner, self.first, settings)
        self.assertEqual(result['local'], settings)
        self.assertEqual(self.git(self.first, 'config', 'custom.keep').strip(), b'untouched')
        (self.first / 'hello.txt').write_text('changed\n')
        self.git(self.first, 'commit', '-am', 'Verify identity')
        self.assertEqual(self.git(self.first, 'log', '-1', '--format=%an <%ae>').strip(), b'New Developer <new@example.invalid>')

    def test_clearing_local_author_inherits_global_without_modifying_it(self):
        global_config = self.base / 'global-git-config'
        global_config.write_text('[user]\n name = Global Developer\n email = global@example.invalid\n')
        before = global_config.read_bytes()
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(global_config), 'GIT_CONFIG_NOSYSTEM': '1'}):
            values = read_settings(self.runner, self.first)['local']
            values['user.name'] = ''; values['user.email'] = ''
            result = save_settings(self.runner, self.first, values)
        self.assertEqual(result['local']['user.name'], '')
        self.assertEqual(result['effective']['user.name'], 'Global Developer')
        self.assertEqual(global_config.read_bytes(), before)

    def test_locked_or_invalid_save_leaves_config_unchanged(self):
        config = self.first / '.git/config'
        before = config.read_bytes()
        values = read_settings(self.runner, self.first)['local']
        bad = dict(values, **{'user.name': 'Injected\nname'})
        with self.assertRaises(ValueError):
            save_settings(self.runner, self.first, bad)
        self.assertEqual(config.read_bytes(), before)
        lock = config.with_name('config.lock'); lock.write_text('another Git operation')
        with self.assertRaisesRegex(RuntimeError, 'locked'):
            save_settings(self.runner, self.first, values)
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(lock.read_text(), 'another Git operation')

    def test_failure_during_temp_save_rolls_back_and_releases_lock(self):
        config = self.first / '.git/config'
        before = config.read_bytes()
        values = read_settings(self.runner, self.first)['local']
        values['user.name'] = 'Should not persist'
        original_run = self.runner.run
        def failing(folder, arguments, **kwargs):
            if '--replace-all' in arguments and 'user.email' in arguments:
                raise RuntimeError('simulated disk failure')
            return original_run(folder, arguments, **kwargs)
        with patch.object(self.runner, 'run', side_effect=failing), self.assertRaises(RuntimeError):
            save_settings(self.runner, self.first, values)
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((self.first / '.git/config.lock').exists())
        self.assertEqual(list((self.first / '.git').glob('.pocket-git-config-*')), [])

    def test_author_value_starting_with_dash_is_saved_literally(self):
        values = read_settings(self.runner, self.first)['local']
        values['user.name'] = '--literal-author'
        result = save_settings(self.runner, self.first, values)
        self.assertEqual(result['local']['user.name'], '--literal-author')

    def test_new_origin_gets_fetch_mapping(self):
        self.git(self.first, 'remote', 'remove', 'origin')
        values = read_settings(self.runner, self.first)['local']
        values['remote.origin.url'] = str(self.origin)
        save_settings(self.runner, self.first, values)
        self.assertEqual(self.git(self.first, 'config', 'remote.origin.fetch').strip(), b'+refs/heads/*:refs/remotes/origin/*')


class SettingsGuiTests(fixtures.TeamGuiTests):
    def test_gui_load_edit_save_reload_and_folder_switch(self):
        self.panel.show_settings(); self.wait_idle()
        self.assertTrue(self.panel.config_loaded)
        self.assertEqual(self.panel.config_vars['user.name'].get(), 'Test')
        self.panel.config_vars['user.name'].set('GUI Developer')
        self.panel.config_vars['user.email'].set('gui@example.invalid')
        self.panel.config_vars['core.autocrlf'].set('input')
        self.panel.save_repository_settings(); self.wait_idle()
        self.assertEqual(self.git(self.first, 'config', '--local', 'user.name').strip(), b'GUI Developer')
        self.panel.close_settings()
        self.panel.show_settings(); self.wait_idle()
        self.assertEqual(self.panel.config_vars['user.name'].get(), 'GUI Developer')
        self.assertEqual(self.panel.config_vars['core.autocrlf'].get(), 'input')
        self.panel.folder.set(str(self.base / 'different'))
        self.assertIsNone(self.panel.config_window)

    def test_failed_load_keeps_save_disabled(self):
        with patch('pocket_editor_plugins.git_project.read_settings', side_effect=RuntimeError('cannot read config')):
            self.panel.show_settings(); self.wait_idle()
        self.assertFalse(self.panel.config_loaded)
        self.assertEqual(str(self.panel.config_save.cget('state')), 'disabled')

    def test_git_setup_detect_choose_verify_and_persist(self):
        from pocket_editor_plugins import _git_executable as git_runtime
        from pathlib import Path
        self.panel.show_git_setup()
        with patch.object(git_runtime, 'SETTINGS_PATH', self.base / 'git_executable_settings.json'):
            self.panel.detect_git(); self.wait_idle()
            self.assertTrue(Path(self.panel.git_path.get()).is_file())
            self.panel.use_git(); self.wait_idle()
            self.assertEqual(git_runtime.saved_executable(), self.panel.runner.executable)
            self.assertIn('saved', self.panel.git_status.cget('text'))
        self.panel.runner.run(self.first, ['status', '--porcelain'])


# These tests reuse the real editor/repository fixture, without rerunning the
# independent Git workflow suite in this module.
for _name in dir(fixtures.TeamGuiTests):
    if _name.startswith('test_') and _name not in SettingsGuiTests.__dict__:
        setattr(SettingsGuiTests, _name, None)
