"""A scrollable group conversation with incoming/outgoing message cards."""
from datetime import datetime
import hashlib
from pathlib import Path
import tkinter as tk
from tkinter import ttk

BG = '#0b141a'
INCOMING = '#202c33'
OUTGOING = '#005c4b'
TEXT = '#e9edef'
MUTED = '#aebac1'
COLORS = ('#53bdeb', '#ffb5b5', '#b5cfff', '#d9b7ff', '#ffcf8a', '#8fdbb5')


class ChatFeed(tk.Frame):
    def __init__(self, parent, download):
        super().__init__(parent, background=BG)
        self.download = download
        self.cards = []
        self.last_date = None
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0, borderwidth=0)
        self.scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = tk.Frame(self.canvas, background=BG)
        self.item = self.canvas.create_window(0, 0, anchor='nw', window=self.inner)
        self.canvas.bind('<Configure>', self.resize)
        self.inner.bind('<Configure>', lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Button-4>', lambda _event: self.canvas.yview_scroll(-3, 'units'))
        self.canvas.bind('<Button-5>', lambda _event: self.canvas.yview_scroll(3, 'units'))
        self.empty = tk.Label(self.inner, text='Start the conversation\nMessages and shared files will appear here.',
                              background=BG, foreground=MUTED, font=('TkDefaultFont', 11), pady=40)
        self.empty.pack(fill='x')

    def wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def resize(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)
        width = max(120, min(540, int(event.width * .72)))
        for card in self.cards:
            card['body'].configure(wraplength=width)

    def clear(self):
        for child in self.inner.winfo_children():
            if child is not self.empty:
                child.destroy()
        self.cards.clear()
        self.last_date = None
        self.empty.pack(fill='x')

    def append(self, rows, own_name):
        near_bottom = self.canvas.yview()[1] >= .96
        if rows:
            self.empty.pack_forget()
        for message in rows:
            mine = message['author'] == own_name
            try:
                stamp = datetime.fromtimestamp(float(message.get('created', 0)))
                day = stamp.strftime('%d %b %Y')
                clock = stamp.strftime('%H:%M')
            except (ValueError, TypeError, OSError, OverflowError):
                day, clock = 'Messages', ''
            if day != self.last_date:
                divider = tk.Frame(self.inner, background=BG)
                divider.pack(fill='x', pady=(10, 6))
                tk.Label(divider, text=day, background='#18252d', foreground=MUTED,
                         padx=12, pady=4, font=('TkDefaultFont', 9)).pack()
                self.last_date = day
            row = tk.Frame(self.inner, background=BG)
            row.pack(fill='x', padx=16, pady=5)
            color = OUTGOING if mine else INCOMING
            bubble = tk.Frame(row, background=color, padx=12, pady=8)
            bubble.pack(side='right' if mine else 'left')
            author = message['author']
            accent = COLORS[hashlib.sha256(author.encode()).digest()[0] % len(COLORS)]
            tk.Label(bubble, text='You' if mine else author, background=color, foreground='#a9e9c9' if mine else accent,
                     anchor='w', font=('TkDefaultFont', 9, 'bold')).pack(anchor='w', pady=(0, 3))
            content = message['text']
            if message.get('file_id'):
                suffix = Path(message['name']).suffix.lower()
                kind = 'AUDIO' if suffix in ('.mp3', '.wav', '.ogg', '.m4a', '.flac') else (
                    'VIDEO' if suffix in ('.mp4', '.webm', '.mov', '.mkv') else 'FILE')
                content = f"{kind}  ·  {message['name']}\n{message['size'] / 1024:.1f} KiB"
            body = tk.Label(bubble, text=content, background=color, foreground=TEXT, justify='left', anchor='w',
                            wraplength=max(120, min(540, int(max(self.canvas.winfo_width(), 300) * .72))),
                            font=('TkDefaultFont', 11))
            body.pack(anchor='w', fill='x')
            for widget in (row, bubble, body):
                widget.bind('<MouseWheel>', self.wheel)
                widget.bind('<Button-4>', lambda _event: self.canvas.yview_scroll(-3, 'units'))
                widget.bind('<Button-5>', lambda _event: self.canvas.yview_scroll(3, 'units'))
            if message.get('file_id'):
                tk.Button(bubble, text='Save attachment ↓', command=lambda item=message: self.download(item),
                          background=color, foreground='#7fdfc7', activebackground=color, activeforeground=TEXT,
                          relief='flat', borderwidth=0, highlightthickness=0, padx=0, pady=5, cursor='hand2').pack(anchor='w')
            tk.Label(bubble, text=clock + (' · sent' if mine else ''), background=color, foreground=MUTED,
                     font=('TkDefaultFont', 8)).pack(anchor='e', pady=(3, 0))
            self.cards.append({'row': row, 'body': body, 'message': message})
            if len(self.cards) > 300:
                self.cards.pop(0)['row'].destroy()
        # Keep date separators bounded too.
        for child in self.inner.winfo_children()[:-620]:
            if not any(card['row'] is child for card in self.cards) and child is not self.empty:
                child.destroy()
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        if near_bottom or any(row['author'] == own_name for row in rows):
            self.canvas.yview_moveto(1)

    def get(self, *_args):
        return '\n'.join(card['message']['author'] + ': ' + (card['message']['text'] or card['message'].get('name', ''))
                         for card in self.cards)

    def destroy(self):
        self.cards.clear()
        super().destroy()
        self.download = None
        self.inner = self.canvas = self.scroll = self.empty = None
