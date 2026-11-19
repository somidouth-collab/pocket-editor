"""Two editable panes using the existing per-document widgets and plugin facade."""
import tkinter as tk
from tkinter import ttk


class SplitEditor:
    def __init__(self, editor):
        self.editor = editor
        self.enabled = False
        self.active_side = 0
        self.documents = [editor.active_document, None]
        panel = editor.editor_panel
        self.divider = ttk.Panedwindow(panel, orient='horizontal')
        self.divider.grid(row=0, column=0, columnspan=3, sticky='nsew')
        self.panes = []
        for side in range(2):
            frame = ttk.Frame(self.divider)
            frame.rowconfigure(1, weight=1)
            frame.columnconfigure(1, weight=1)
            title = ttk.Label(frame, padding=(10, 5), anchor='w')
            title.grid(row=0, column=0, columnspan=3, sticky='ew')
            title.bind('<Button-1>', lambda event, side=side: self.focus(side))
            if side == 0:
                gutter, vertical, horizontal = editor.gutter, editor.vertical_scrollbar, editor.horizontal_scrollbar
            else:
                gutter = tk.Canvas(panel, width=40, highlightthickness=0, borderwidth=0, takefocus=False)
                vertical = ttk.Scrollbar(panel, orient='vertical')
                horizontal = ttk.Scrollbar(panel, orient='horizontal')
            self.panes.append((frame, title, gutter, vertical, horizontal))
        self.divider.add(self.panes[0][0], weight=1)
        self.layout()

    def focus(self, side):
        document = self.documents[side]
        if document:
            self.editor.select_document(document)
            document['widget'].focus_set()

    def show_document(self, document):
        if not self.enabled:
            self.documents = [document, None]
            self.active_side = 0
        elif any(document is candidate for candidate in self.documents):
            self.active_side = next(side for side, candidate in enumerate(self.documents) if candidate is document)
        else:
            self.documents[self.active_side] = document
        self.layout()

    def enable(self):
        if self.enabled:
            self.focus(1 - self.active_side)
            return
        current = self.editor.active_document
        if len(self.editor.documents) == 1:
            self.editor.new()
            other = self.editor.active_document
        else:
            index = self.editor.documents.index(current)
            other = self.editor.documents[(index + 1) % len(self.editor.documents)]
        self.enabled = True
        self.documents = [current, other]
        self.active_side = 1
        self.divider.add(self.panes[1][0], weight=1)
        self.editor.select_document(other)
        self.layout()
        self.divider.update_idletasks()
        self.center()

    def center(self):
        if self.enabled and not self.editor.closing:
            self.divider.sashpos(0, max(1, self.divider.winfo_width() // 2))

    def close(self):
        if not self.enabled:
            return
        self.enabled = False
        self.divider.forget(self.panes[1][0])
        self.documents = [self.editor.active_document, None]
        self.active_side = 0
        self.layout()
        self.editor.buffer.focus_set()

    def other(self):
        if self.enabled:
            self.focus(1 - self.active_side)

    def document_closed(self, document):
        if len(self.editor.documents) < 2:
            self.close()
        elif self.enabled and any(document is candidate for candidate in self.documents):
            side = next(side for side, candidate in enumerate(self.documents) if candidate is document)
            self.documents[side] = next(candidate for candidate in self.editor.documents
                                        if all(candidate is not visible for visible in self.documents))
        self.show_document(self.editor.active_document)

    def layout(self):
        editor = self.editor
        visible = self.documents if self.enabled else self.documents[:1]
        for document in editor.documents:
            if all(document is not candidate for candidate in visible):
                document['widget'].grid_remove()
        for side, (frame, title, gutter, vertical, horizontal) in enumerate(self.panes):
            document = visible[side] if side < len(visible) else None
            if document is None:
                for widget in (gutter, vertical, horizontal):
                    widget.grid_remove()
                continue
            text = document['widget']
            path = document['path']
            title.configure(text=('●  ' if text.edit_modified() or document['rich_dirty'] else '') +
                            (path.name if path else 'Untitled'),
                            foreground='#ffffff' if document is editor.active_document else '#bdbdbd',
                            background='#10151b' if document is editor.active_document else '#19212a')
            if self.enabled:title.grid()
            else:title.grid_remove()
            text.grid(in_=frame, row=1, column=1, sticky='nsew')
            gutter.configure(background=text.cget('background'))
            gutter.grid(in_=frame, row=1, column=0, sticky='ns')
            if not editor.line_numbers.get():
                gutter.grid_remove()
            vertical.configure(command=text.yview)
            horizontal.configure(command=text.xview)
            vertical.grid(in_=frame, row=1, column=2, sticky='ns')
            horizontal.grid(in_=frame, row=2, column=1, sticky='ew')
            # grid(in_=...) changes geometry ownership, not widget stacking.
            # The initial buffer and its controls predate the pane container.
            for widget in (text, gutter, vertical, horizontal):
                widget.tk.call('raise', str(widget))
            text.configure(yscrollcommand=lambda first, last, bar=vertical: self.scrolled(bar, first, last),
                           xscrollcommand=horizontal.set)
            vertical.set(*text.yview()); horizontal.set(*text.xview())
        frame, title, editor.gutter, editor.vertical_scrollbar, editor.horizontal_scrollbar = self.panes[self.active_side]
        editor.queue_gutter()

    def scrolled(self, scrollbar, first, last):
        scrollbar.set(first, last)
        self.editor.queue_gutter()

    def draw_gutters(self):
        editor = self.editor
        for side, document in enumerate(self.documents):
            if document is None or side == 1 and not self.enabled:
                continue
            text, gutter = document['widget'], self.panes[side][2]
            gutter.delete('all')
            last_line = text.index('end-1c').split('.')[0]
            width = editor.gutter_font.measure('0' * max(2, len(last_line))) + 16
            gutter.configure(width=width, background=text.cget('background'))
            index = text.index('@0,0 display linestart')
            while info := text.dlineinfo(index):
                line, column = index.split('.')
                if column == '0':
                    gutter.create_text(width - 7, info[1] + info[4] - editor.gutter_font.metrics('ascent'),
                                       text=line, anchor='ne', font=editor.gutter_font,
                                       fill=text.cget('foreground') if line == text.index('insert').split('.')[0] else editor.gutter_ink)
                following = text.index(f'{index} +1 display lines')
                if text.compare(following, '>=', 'end') or text.compare(following, '<=', index):
                    break
                index = following
