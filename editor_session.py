"""Session snapshots and crash recovery; enabled by the application entry point."""
import json
import tkinter as tk
from pathlib import Path
from tkinter import font, messagebox


class EditorSession:
    def __init__(self, editor, path):
        self.editor, self.path = editor, Path(path)
        self.job = None
        self.lock = None
        self.fonts = []
        self.error_reported = False

    def start(self):
        # A second editor instance must not overwrite the first one's recovery data.
        try:
            self.lock = self.path.with_suffix('.lock').open('a+b')
            import os
            if os.name == 'nt':
                import msvcrt
                self.lock.seek(0)
                self.lock.write(b'0'); self.lock.flush(); self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError):
            if self.lock:
                self.lock.close()
            self.lock = None
            return False
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.restore(data)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, tk.TclError) as error:
            messagebox.showwarning('Session restore', f'Could not restore the previous session: {error}', parent=self.editor.root)
            # Preserve an unreadable snapshot for manual recovery before replacing it.
            try:
                import shutil
                shutil.copy2(self.path, self.path.with_suffix('.invalid.json'))
            except OSError:
                self.release()
                return False
        self.editor.session = self
        self.checkpoint()
        return True

    def snapshot(self, clean=False):
        editor = self.editor
        editor.remember_document()
        entries = []
        active = 0
        for document in editor.documents:
            path = document['path']
            if clean and path is None:
                continue
            text = document['widget']
            if document is editor.active_document:
                active = len(entries)
            item = {'path': str(path) if path else None, 'cursor': text.index('insert'),
                    'view': text.yview()[0], 'newline': document['newline'], 'bom': document['bom']}
            if not clean and (text.edit_modified() or document['rich_dirty'] or path is None):
                item.update(text=text.get('1.0', 'end-1c'), dirty=bool(text.edit_modified()),
                            rich_dirty=document['rich_dirty'], styles=[])
                for tag in text.tag_names():
                    ranges = list(map(str, text.tag_ranges(tag)))
                    if not tag.startswith('rich_') or not ranges:
                        continue
                    options = {key: value[-1] for key, value in text.tag_configure(tag).items() if value[-1] != ''}
                    if options.get('font'):
                        options['font_spec'] = font.Font(root=editor.root, font=options.pop('font')).actual()
                    item['styles'].append({'style': options, 'ranges': ranges})
            entries.append(item)
        return {'version': 1, 'clean': clean, 'active': active, 'documents': entries}

    def write(self, clean=False):
        try:
            data = json.dumps(self.snapshot(clean), ensure_ascii=False).encode('utf-8')
            self.editor.write_data(self.path, data)
            return True
        except (OSError, ValueError, TypeError) as error:
            if not self.error_reported:
                messagebox.showwarning('Session backup', f'Could not save session recovery data: {error}', parent=self.editor.root)
                self.error_reported = True
            return False

    def checkpoint(self):
        self.job = None
        if not self.editor.closing:
            self.write()
            self.job = self.editor.root.after(5000, self.checkpoint)

    def restore(self, data):
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('documents'), list):
            raise ValueError('Unsupported session format')
        entries = data['documents']
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get('cursor', '1.0'), str):
                raise ValueError('Invalid document entry')
            if item.get('path') is not None and not isinstance(item['path'], str):
                raise ValueError('Invalid file path')
            if 'text' in item and not isinstance(item['text'], str):
                raise ValueError('Invalid recovery text')
            if item.get('newline', '\n') not in ('\n', '\r', '\r\n'):
                raise ValueError('Invalid newline convention')
            if not isinstance(item.get('styles', []), list):
                raise ValueError('Invalid formatting')
            for span in item.get('styles', []):
                if not isinstance(span, dict) or not isinstance(span.get('style'), dict) or not isinstance(span.get('ranges'), list):
                    raise ValueError('Invalid formatting span')
                ranges = span['ranges']
                if len(ranges) % 2 or not all(isinstance(index, str) for index in ranges):
                    raise ValueError('Invalid formatting ranges')
        recover = any('text' in item for item in entries)
        if recover:
            recover = messagebox.askyesno('Recover unsaved documents',
                'The previous session contains unsaved drafts. Recover them?\nFiles on disk will not change until you save.', parent=self.editor.root)
        restored = {}
        skipped = []
        editor = self.editor
        for index, item in enumerate(entries):
            path = Path(item['path']) if item.get('path') else None
            draft = recover and 'text' in item
            if not draft and path is None:
                continue
            if draft:
                if restored or editor.path is not None or editor.buffer.get('1.0', 'end-1c') or editor.buffer.edit_modified():
                    editor.new()
                editor.set_document(path, item['text'], item.get('newline', '\n'), bool(item.get('bom')))
                editor.rich_dirty = bool(item.get('rich_dirty'))
                for number, span in enumerate(item.get('styles', [])):
                    options = dict(span['style'])
                    if 'font_spec' in options:
                        instance = font.Font(root=editor.root, **options.pop('font_spec'))
                        self.fonts.append(instance)
                        options['font'] = instance
                    tag = f'rich_recovered_{number}'
                    editor.buffer.tag_configure(tag, **options)
                    editor.buffer.tag_add(tag, *span['ranges'])
                editor.buffer.edit_modified(bool(item.get('dirty')))
                editor.dispatch_plugin_event('document_styles_restored', 'editor', {})
            else:
                if not path.is_file():
                    skipped.append(str(path))
                    continue
                editor.open_path(path)
                if editor.path != path:
                    continue
            restored[index] = editor.active_document
            try:
                editor.buffer.mark_set('insert', item.get('cursor', '1.0'))
                editor.buffer.yview_moveto(float(item.get('view', 0)))
            except (ValueError, TypeError, tk.TclError):
                editor.buffer.mark_set('insert', '1.0')
            editor.refresh()
        if data.get('active') in restored:
            editor.select_document(restored[data['active']])
        if skipped:
            messagebox.showwarning('Session restore', 'Missing files were skipped:\n' + '\n'.join(skipped[:10]), parent=editor.root)

    def finish(self):
        if self.job:
            self.editor.root.after_cancel(self.job)
            self.job = None
        self.write(clean=True)
        self.release()

    def release(self):
        if self.lock:
            self.lock.close()
            self.lock = None
