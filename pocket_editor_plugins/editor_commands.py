"""Everyday editing, navigation, tab, and clipboard commands for the palette."""
from tkinter import messagebox, simpledialog


def line_range(text):
    selection = text.tag_ranges('sel')
    first = text.index(selection[0] if selection else 'insert')
    last = text.index(f'{selection[1]} -1c' if selection else 'insert')
    return int(first.split('.')[0]) - 1, int(last.split('.')[0]) - 1, bool(selection)


def edit_lines(editor, operation):
    text = editor.buffer
    lines = text.get('1.0', 'end-1c').split('\n')
    first, last, selected = line_range(text)
    cursor_line, cursor_column = map(int, text.index('insert').split('.'))
    start, stop = first, last
    block = lines[first:last + 1]
    if operation == 'duplicate':
        replacement = '\n'.join(block + block)
        destination = first + len(block)
    elif operation == 'up':
        if first == 0:
            return
        start -= 1
        replacement = '\n'.join(block + [lines[start]])
        destination = first - 1
    elif operation == 'down':
        if last == len(lines) - 1:
            return
        stop += 1
        replacement = '\n'.join([lines[stop]] + block)
        destination = first + 1
    elif operation == 'delete':
        text.edit_separator()
        if last < len(lines) - 1:
            text.delete(f'{first + 1}.0', f'{last + 2}.0')
        elif first:
            text.delete(f'{first}.end', 'end-1c')
        else:
            text.delete('1.0', 'end-1c')
        text.edit_separator()
        text.mark_set('insert', f'{min(first + 1, int(text.index("end-1c").split(".")[0]))}.0')
        text.see('insert')
        editor.refresh()
        return
    else:
        raise ValueError('Unknown line operation')
    text.edit_separator()
    text.replace(f'{start + 1}.0', f'{stop + 1}.end', replacement)
    text.edit_separator()
    text.tag_remove('sel', '1.0', 'end')
    if selected:
        text.tag_add('sel', f'{destination + 1}.0', f'{destination + len(block)}.end')
    text.mark_set('insert', f'{cursor_line + destination - first}.{cursor_column}')
    text.see('insert')
    editor.refresh()


def register(api):
    editor = api._editor

    def go_to_line():
        last = int(editor.buffer.index('end-1c').split('.')[0])
        number = simpledialog.askinteger('Go to Line', f'Line number (1–{last})',
                                         parent=editor.root, minvalue=1, maxvalue=last)
        if number is not None:
            editor.buffer.tag_remove('sel', '1.0', 'end')
            editor.buffer.mark_set('insert', f'{number}.0')
            editor.buffer.see('insert')
            editor.buffer.focus_set()
            editor.refresh()

    def trim_spaces():
        text = editor.buffer
        lines = api.get_text().split('\n')
        text.edit_separator()
        for index in range(len(lines), 0, -1):
            stripped = lines[index - 1].rstrip(' \t')
            if stripped != lines[index - 1]:
                text.delete(f'{index}.end -{len(lines[index - 1]) - len(stripped)}c', f'{index}.end')
        text.edit_separator()
        editor.refresh()

    def copy_path(folder=False):
        path = api.get_path()
        if path is None:
            messagebox.showinfo('Copy Path', 'Save this file first to give it a path.', parent=editor.root)
            return
        editor.root.clipboard_clear()
        editor.root.clipboard_append(str(path.parent if folder else path))

    def cycle_tab(delta):
        documents = editor.documents
        index = documents.index(editor.active_document)
        editor.select_document(documents[(index + delta) % len(documents)])

    def close_others():
        current = editor.active_document
        for document in tuple(editor.documents):
            if document is not current and not editor.close_document(document):
                break

    def zoom(delta=None):
        size = 11 if delta is None else max(8, min(40, editor.editor_font.cget('size') + delta))
        editor.editor_font.configure(size=size)
        editor.save_settings()

    for label, callback in (
        ('Duplicate Line / Selection', lambda: edit_lines(editor, 'duplicate')),
        ('Move Lines Up', lambda: edit_lines(editor, 'up')),
        ('Move Lines Down', lambda: edit_lines(editor, 'down')),
        ('Delete Lines', lambda: edit_lines(editor, 'delete')),
        ('Trim Trailing Whitespace', trim_spaces),
        ('Go to Line…', go_to_line),
        ('Copy File Path', copy_path),
        ('Copy Folder Path', lambda: copy_path(True)),
        ('Next Tab', lambda: cycle_tab(1)),
        ('Previous Tab', lambda: cycle_tab(-1)),
        ('Close Other Tabs', close_others),
        ('Zoom In', lambda: zoom(1)),
        ('Zoom Out', lambda: zoom(-1)),
        ('Reset Zoom', zoom),
    ):
        api.add_command(label, callback)
