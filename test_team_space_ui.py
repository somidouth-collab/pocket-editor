"""Modern group conversation layout and independent Team Project entry point."""
import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pocket_editor as core
from pocket_editor_plugins.team_project import TeamProject


class TeamSpaceTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.root = tk.Tk()
        self.root.withdraw()
        editor = SimpleNamespace(root=self.root, plugin_apis={}, documents=[], remember_document=lambda: None)
        self.api = core.PluginAPI(editor, 'team_project')
        self.panel = TeamProject(self.api)
        self.panel.show()
        self.root.update()
        self.chat = self.panel.chat

    def tearDown(self):
        self.panel.cleanup()
        self.chat.worker.thread.join(timeout=3)
        self.root.destroy()

    def test_team_plugin_has_no_git_panel_and_compact_controls_fit(self):
        self.assertFalse(hasattr(self.panel, 'runner'))
        self.assertEqual([label for label, _callback in self.api._commands], ['Team Project'])
        self.panel.window.geometry('720x620')
        self.chat.show_settings(False)
        self.root.update()
        for widget in (self.chat.composer, self.chat.send_button, self.chat.attach_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                 self.panel.window.winfo_rooty() + self.panel.window.winfo_height())
        self.chat.pages.select(self.chat.files_tab)
        self.root.update()
        self.assertTrue(self.chat.download_button.winfo_ismapped())
        self.chat.toggle_settings()
        self.root.update()
        self.assertTrue(self.chat.load_invite_button.winfo_ismapped())

    def test_bubbles_align_by_sender_timestamps_and_attachment_button(self):
        self.chat.author.set('Alice')
        now = time.time()
        attachment = {'id': 3, 'author': 'Bob', 'text': '', 'created': now,
                      'file_id': 'a' * 32, 'name': 'team-video.mp4', 'size': 512, 'sha256': 'b' * 64}
        self.chat.append([{'id': 1, 'author': 'Bob', 'text': 'Incoming', 'created': now},
                          {'id': 2, 'author': 'Alice', 'text': 'Outgoing', 'created': now}, attachment])
        cards = self.chat.history.cards
        self.assertEqual(cards[0]['row'].winfo_children()[0].pack_info()['side'], 'left')
        self.assertEqual(cards[1]['row'].winfo_children()[0].pack_info()['side'], 'right')
        self.assertIn('VIDEO', cards[2]['body'].cget('text'))
        self.chat.history.download = Mock()
        bubble = cards[2]['row'].winfo_children()[0]
        button = next(child for child in bubble.winfo_children() if isinstance(child, tk.Button))
        button.invoke()
        self.chat.history.download.assert_called_once_with(attachment)
        self.chat.history.clear()
        self.assertEqual(self.chat.history.cards, [])
        self.assertEqual(len(self.chat.history.inner.winfo_children()), 1)
