"""Hosted team projects, client invitations, group chat, and shared files."""
from pocket_editor_plugins._docking import create_window
from pathlib import Path
import tkinter as tk
from pocket_editor_plugins._team_chat_ui import ChatPane


def register(api):
    TeamProject(api)


class TeamProject:
    def __init__(self, api):
        self.api = api
        self.root = api._editor.root
        self.window = None
        self.chat = None
        self.closed = False
        api.add_command("Team Project", self.show)
        api.provide_service("open_project", self.open_project)
        api.add_panel(lambda parent: self.show(parent), width=720)
        api.on_cleanup(self.cleanup)

    def show(self, parent=None):
        if self.closed:
            return
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return
        self.window = create_window(self.root, parent)
        self.window.title("Team Project")
        self.window.geometry("900x780")
        self.window.minsize(720, 620)
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self.chat = ChatPane(self.window, self.root, editor=self.api._editor)
        self.chat.pack(fill="both", expand=True)
        try:
            folder = self.api.call_service("project_explorer.get_project")
        except LookupError:
            folder = None
        if folder:
            self.chat.project.set(str(folder))

    def open_project(self, folder):
        self.show()
        if self.chat.connected or self.chat.connecting:
            raise ValueError("Leave the current team session before choosing another project.")
        folder = Path(folder).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("Choose an existing project folder.")
        self.chat.project.set(str(folder))

    def cleanup(self):
        if self.closed:
            return
        self.closed = True
        if self.chat is not None:
            self.chat.cleanup()
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
