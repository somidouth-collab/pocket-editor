"""Add a command that counts words in the current document."""
from tkinter import messagebox


def register(api):
    def count_words():
        count = len(api.get_text().split())
        messagebox.showinfo("Word count", f"{count:,} words")

    api.add_command("Count words", count_words)
