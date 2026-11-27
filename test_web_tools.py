"""Web Tools integration checks against real local language services and Tk."""
from pathlib import Path
import json
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
from concurrent.futures import Future
import tkinter as tk
from tkinter import ttk
import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins import _web_tools as backend
from pocket_editor_plugins.web_tools import WebTools
from pocket_editor_plugins._php_completion import prepare_completion

READY=bool(shutil.which('node') and (backend.TOOLS/'node_modules/typescript/package.json').is_file())


@unittest.skipUnless(READY,'Install optional web tools: npm ci --prefix tools/web --ignore-scripts')
class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.worker=backend.WebWorker()
    def tearDown(self):self.worker.close();self.temp.cleanup()
    def request(self,kind,source,suffix,offset=None,**options):
        payload=dict(operation=kind,source=source,language=backend.language('demo'+suffix),
            path=str(self.folder/('demo'+suffix)),root=str(self.folder),
            offset=len(source[:len(source) if offset is None else offset].encode('utf-16-le'))//2)
        payload.update(options);return self.worker.request(payload)
    def test_real_context_completion_and_embedded_languages(self):
        for source,suffix,expected in (('<di','.html','div'),('a { dis','.css','display'),
                ('const user = { name: "Ada" }; user.na','.js','name'),
                ('document.que','.js','querySelector')):
            with self.subTest(suffix=suffix,source=source):
                self.assertIn(expected,[i['label'] for i in self.request('complete',source,suffix)])
        source='<style>a { dis }</style>'
        self.assertIn('display',[i['label'] for i in self.request('complete',source,'.html',source.index('dis')+3)])
        source='<script>document.que</script>'
        self.assertIn('querySelector',[i['label'] for i in self.request('complete',source,'.html',source.index('que')+3)])
    def test_project_imports_and_unsaved_open_files(self):
        other=self.folder/'other.js';other.write_text('export const user = { name: "Ada" };')
        source='import { user } from "./other.js"; user.na'
        self.assertIn('name',[i['label'] for i in self.request('complete',source,'.js')])
        source='import { user } from "./other.js"; user.ag'
        result=self.request('complete',source,'.js',documents=[dict(path=str(other),source='export const user = { age: 37 };')])
        self.assertIn('age',[i['label'] for i in result])
    def test_formatting_idempotence_and_invalid_source(self):
        for source,suffix in (('<main><h1>Hello</h1><p>World</p></main>','.html'),
                ('a{color:red;display:flex}','.css'),('const value={a:1,b:2}','.js')):
            with self.subTest(suffix=suffix):
                formatted=self.request('format',source,suffix)['formatted']
                self.assertTrue(formatted.endswith('\n'))
                self.assertEqual(formatted,self.request('format',formatted,suffix)['formatted'])
        with self.assertRaises(RuntimeError):self.request('format','const = ;','.js')
        self.assertTrue(self.request('check','const = ;','.js'))
        self.assertTrue(self.request('check','a { color: }','.css'))
    def test_html_tag_brackets_preserve_inline_text_and_cursor(self):
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self,source):super().__init__();self.parts=[];self.feed(source)
            def handle_data(self,data):self.parts.append(data)
        source='<a class="brand" href="index.html"\n        ><b class="brand-mark">P</b><span>Pocket Editor <small>DOCUMENTATION</small></span></a\n      >'
        result=self.request('format',source,'.html',source.index('DOCUMENTATION')+4,width=80)
        formatted=result['formatted']
        self.assertNotRegex(formatted,r'\n[ \t]*>')
        self.assertIn('</a>',formatted)
        self.assertEqual(''.join(Text(formatted).parts).strip(),''.join(Text(source).parts).strip())
        self.assertTrue(formatted[result['cursorOffset']:].startswith('MENTATION'))
        source='<pre>literal\n  >\ntext</pre><!-- keep\n > -->'
        formatted=self.request('format',source,'.html')['formatted']
        self.assertIn('literal\n  >\ntext',formatted)
        self.assertIn('<!-- keep\n > -->',formatted)

    def test_actual_editor_docs_no_dangling_brackets_and_idempotent(self):
        from html.parser import HTMLParser
        class Tags(HTMLParser):
            def __init__(self,source):super().__init__();self.tags=[];self.feed(source)
            def handle_starttag(self,tag,attrs):self.tags.append((tag,attrs))
            def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs)
        docs=Path(__file__).parent/'docs'
        files=list(docs.glob('*.html'));self.assertGreaterEqual(len(files),18)
        for path in files:
            with self.subTest(page=path.name):
                source=path.read_text(encoding='utf-8')
                formatted=self.request('format',source,'.html')['formatted']
                self.assertNotRegex(formatted,r'\n[ \t]*>')
                self.assertEqual(Tags(source).tags,Tags(formatted).tags)
                self.assertEqual(formatted,self.request('format',formatted,'.html')['formatted'])

    def test_unicode_completion_edit_ranges(self):
        source='/* 😀 */ a { dis'
        item=next(i for i in self.request('complete',source,'.css') if i['label']=='display')
        edits,caret,stops=prepare_completion(source,len(source),item)
        result=source
        for start,end,value,_primary in reversed(edits):result=result[:start]+value+result[end:]
        self.assertIn('😀',result);self.assertIn('display',result);self.assertNotIn('disdisplay',result)
    def test_process_cleanup_and_restart(self):
        self.request('complete','<di','.html');old=self.worker.process
        self.worker.stop_process();self.assertIsNotNone(old.poll())
        self.assertIn('div',[i['label'] for i in self.request('complete','<di','.html')])


class SettingsTests(unittest.TestCase):
    def test_settings_validation_and_language_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';path.write_text('{"automatic":"yes","indent":99,"width":10000,"semi":false}')
            settings=backend.read_settings(path)
            self.assertTrue(settings['automatic']);self.assertEqual(settings['indent'],2);self.assertEqual(settings['width'],100);self.assertFalse(settings['semi'])
        self.assertEqual(backend.language('INDEX.HTML'),'html');self.assertIsNone(backend.language('template.fpf'))
        self.assertIsNone(backend.language(None))
    def test_html_css_theme_spans(self):
        from pocket_editor_plugins import code_theme
        self.assertEqual(code_theme.EXTENSIONS['.html'],'HTML')
        self.assertEqual(code_theme.EXTENSIONS['.css'],'CSS')
        html=list(code_theme.spans('<main class="hero"><!-- hi --></main>','HTML'))
        self.assertTrue({'keyword','function','string','comment'} <= {kind for kind,_,_ in html})
        css=list(code_theme.spans('.hero { color: #fff; /* hi */ }','CSS'))
        self.assertTrue({'keyword','function','number','comment'} <= {kind for kind,_,_ in css})

    def test_html_opening_and_closing_delimiters_have_same_color(self):
        from pocket_editor_plugins import code_theme
        source='<a title="x > y">Text > text</a><br />'
        spans=list(code_theme.spans(source,'HTML'))
        def kind_at(offset):
            return next((kind for kind,start,end in spans if start<=offset<end),None)
        for start,end in ((0,source.index('>Text')),(source.index('</a'),source.index('</a')+3),
                          (source.index('<br'),len(source)-1)):
            self.assertEqual(kind_at(start),'keyword')
            self.assertEqual(kind_at(end),'keyword')
        self.assertEqual(kind_at(source.index('x > y')+2),'string')
        self.assertIsNone(kind_at(source.index('Text > text')+5))
        self.assertEqual(kind_at(source.index('/>')),'keyword')

    def test_missing_node_is_actionable(self):
        worker=backend.WebWorker()
        with patch.object(backend.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'Node.js'):worker.start()
        worker.close()


@unittest.skipUnless(READY,'Install optional web tools for GUI integration checks')
class GuiTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.api=core.PluginAPI(self.editor,'web_tools');self.editor.plugin_apis['web_tools']=self.api
        self.app=WebTools(self.api)
        self.path=core.SETTINGS_PATH.parent/'demo.js'
    tearDown=fixtures.PaletteTests.tearDown
    def pump(self,seconds=.05):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.root.update();time.sleep(.005)
    def until(self,predicate,timeout=15):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:self.pump()
        self.assertTrue(predicate(),self.app.status.get())
    def test_real_format_is_undoable_and_does_not_save(self):
        source='const value={a:1}'
        self.editor.set_document(self.path,source);self.path.write_text('saved file unchanged')
        self.app.format_document();self.until(lambda:not self.app.jobs)
        self.assertEqual(self.api.get_text(),'const value = { a: 1 };\n')
        self.editor.buffer.edit_undo();self.assertEqual(self.api.get_text(),source)
        self.assertEqual(self.path.read_text(),'saved file unchanged')
    def test_completion_accepts_real_member_and_rejects_stale_popup(self):
        self.editor.set_document(self.path,'document.que');self.editor.buffer.mark_set('insert','end-1c')
        self.editor.buffer.focus_force();self.root.update();self.app.complete();self.until(lambda:bool(self.app.items))
        i=next(i for i,item in enumerate(self.app.items) if item['label']=='querySelector')
        self.app.listing.selection_clear(0,'end');self.app.listing.selection_set(i);self.app.accept()
        self.assertEqual(self.api.get_text(),'document.querySelector')
        self.editor.buffer.edit_undo();self.assertEqual(self.api.get_text(),'document.que')
        capture=self.app.capture();self.app.suggestions([dict(label='wrong',insertText='wrong')],capture)
        self.editor.buffer.insert('end-1c','x');before=self.api.get_text();self.app.accept()
        self.assertEqual(self.api.get_text(),before)
    def test_stale_async_format_and_escape_do_not_apply_results(self):
        self.editor.set_document(self.path,'const a=1');capture=self.app.capture();future=Future()
        self.app.jobs[future]=('format',capture,None,True)
        self.editor.buffer.insert('end-1c','; // new');future.set_result(dict(formatted='wrong',cursorOffset=0))
        self.until(lambda:not self.app.jobs);self.assertEqual(self.api.get_text(),'const a=1; // new')
        self.editor.buffer.mark_set('insert','end-1c');capture=self.app.capture();future=Future()
        self.app.jobs[future]=('complete',capture,None,True);self.app.escape()
        future.set_result([dict(label='wrong')]);self.until(lambda:not self.app.jobs);self.assertFalse(self.app.items)
    def test_snippet_tabstops_and_syntax_markers(self):
        self.editor.set_document(self.path.with_suffix('.css'),'a { dis')
        self.editor.buffer.mark_set('insert','end-1c');self.editor.buffer.focus_force();self.root.update()
        capture=self.app.capture()
        item=dict(label='display',insertTextFormat=2,textEdit=dict(newText='display: ${1:flex};$0',
            range=dict(start=dict(line=0,character=4),end=dict(line=0,character=7))))
        self.app.suggestions([item],capture);self.app.accept()
        self.assertEqual(self.editor.buffer.get('sel.first','sel.last'),'flex')
        self.assertEqual(self.api.get_text(),'a { display: flex;')
        self.app.tab();self.assertFalse(self.editor.buffer.tag_ranges('sel'))
        self.editor.buffer.edit_undo();self.assertEqual(self.api.get_text(),'a { dis')
        self.editor.set_document(self.path,'const = ;');self.app.request('check')
        self.until(lambda:not self.app.jobs)
        self.assertTrue(self.app.issues)
        self.app.jump(self.app.issues[0]);self.assertEqual(self.api.get_text(),'const = ;')

    def test_panel_settings_and_cleanup(self):
        self.app.show();sidebar=self.editor.plugin_sidebar
        self.until(lambda:'web_tools' in sidebar.hosts and 'web_tools' not in sidebar.pending)
        panel=sidebar.hosts['web_tools']._panel_widget
        labels={w.cget('text') for w in self.descendants(panel) if isinstance(w,ttk.Button)}
        self.assertIn('Install / Repair Tools',labels);self.assertIn('Save settings',labels)
        self.app.variables['indent'].set('4');self.app.save_settings()
        self.assertEqual(json.loads(self.app.settings_path.read_text())['indent'],4)
        self.editor.set_document(self.path,'const a=1');self.app.format_document();self.until(lambda:not self.app.jobs)
        process=self.app.worker.process;self.app.cleanup();self.assertIsNotNone(process.poll())
    def descendants(self,parent):
        for child in parent.winfo_children():yield child;yield from self.descendants(child)
