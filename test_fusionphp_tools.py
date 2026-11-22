"""Offline .fpf grammar, safe formatting, completion, themes and actual Tk errors."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import pocket_editor as core
from pocket_editor_plugins import _fusionphp as fpf, code_theme
from pocket_editor_plugins.fusionphp_tools import read_settings
from pocket_editor_plugins._php_completion import prepare_completion
import test_command_palette as fixtures

GOOD=[
    '', '<p>{{ title }}</p>', '<p>{{ trim title }} {{ length items }}</p>',
    '<p>{{ default title "Hello }} there" }}</p>', "<p>{{ join items ', ' }}</p>",
    '{% if account.active %}<p>Hello</p>{% else %}<p>Bye</p>{% endif %}',
    '{% for item in items %}{{ item.name }}{% endfor %}',
    '{% extends "main" %}\n{% block content %}<h1>{{ title }}</h1>{% endblock %}',
    '<a href="{{ url target }}" title="{{ default title \'Link\' }}">{{ title }}</a>',
    '<input disabled value="{{ name }}">', '<div data-count="3">ok</div>',
    '<!-- literal comment --><!doctype html><p>ok &lt; 3</p>',
]
BAD=[
    '{{ missing', '{% if field %}', '{% else %}', '{{ field() }}', '{{ user[0] }}',
    '{% if user == true %}yes{% endif %}', '{% foreach items as item %}{% endforeach %}',
    '{% block content %}x{% endblock %}', '{% extends \'main\' %}',
    '{% extends "main" %}<p>outside</p>',
    '{% extends "main" %}{% block x %}a{% endblock %}{% block x %}b{% endblock %}',
    '<a href="{{ target }}">bad</a>', '<p>{{ url target }}</p>',
    '<p class="prefix {{ name }}">bad</p>', '<p onclick="{{ action }}">bad</p>',
    '<div {% if x %}>bad</div>', '<script>alert(1)</script>', '<style>p{color:red}</style>',
    '<?php echo 1; ?>', '<p>1 < 2</p>', '<a href={{ url link }}>x</a>',
    '<!-- {{ interpolated }} -->', '<p>{{ default title 4 }}</p>',
]


class BackendTests(unittest.TestCase):
    def test_runtime_grammar_valid_and_invalid_cases(self):
        for source in GOOD:
            with self.subTest(source=source):self.assertEqual(fpf.check_source(source,Path('page.fpf')),[])
        for source in BAD:
            with self.subTest(source=source):self.assertTrue(fpf.check_source(source,Path('page.fpf')))
        self.assertEqual(fpf.check_source('{% yield content %}',Path('app/layouts/main.fpf')),[])
        self.assertTrue(fpf.check_source('{% yield content %}',Path('app/page.fpf')))
        self.assertTrue(fpf.check_source('{% extends "main" %}',Path('app/layouts/other.fpf')))
        nested='{% if flag %}'*65+'x'+'{% endif %}'*65
        self.assertTrue(fpf.check_source(nested))
        with self.assertRaises(ValueError):fpf.check_source('x'*(fpf.MAX_SOURCE+1))

    def test_formatter_preserves_literals_quotes_newlines_and_is_idempotent(self):
        source='<section>\r\n<div>\r\n<h1>{{ default title "Hi" }}</h1>\r\n</div>\r\n</section>\r\n'
        formatted=fpf.format_source(source,indent=2)
        self.assertIn('\r\n  <div>\r\n    <h1>',formatted)
        self.assertEqual(fpf.format_source(formatted,indent=2),formatted)
        protected='<pre>\n  keep  exactly\n    <code>{{ code }}</code>\n</pre>'
        text='<section>\n'+protected+'\n<textarea>  exact\n  text</textarea>\n<div title="line one\n  line two">ok</div>\n</section>'
        result=fpf.format_source(text)
        self.assertIn(protected,result);self.assertIn('<textarea>  exact\n  text</textarea>',result)
        self.assertIn('title="line one\n  line two"',result)
        self.assertEqual(fpf.check_source(result),[])
        with self.assertRaises(fpf.TemplateError):fpf.format_source('{% if missing %}')

    def test_static_fields_loop_scopes_directives_and_url_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'app/home').mkdir(parents=True);(root/'app/layouts').mkdir()
            (root/'app/home/HomeModel.php').write_text("<?php return ['title'=>'Demo','guides'=>[['title'=>'One','url'=>'/one']]];")
            (root/'app/config.php').write_text("<?php return ['secretPassword'=>'private'];")
            (root/'app/layouts/docs.fpf').write_text('{% yield content %}')
            path=root/'app/home/home.fpf'
            source='{% for guide in guides %}\n{{ guide.'
            self.assertEqual({item['label'] for item in fpf.completion_items(source,len(source),path,root)}, {'guide.title','guide.url'})
            source='{% for guide in guides %}x{% endfor %}\n{{ guide.'
            self.assertEqual(fpf.completion_items(source,len(source),path,root),[])
            source='{{ secret'
            self.assertEqual(fpf.completion_items(source,len(source),path,root),[])
            for source,label in (('{% ex','extends'),('{% i','if'),('{{ def','default'),('<a href="{{ u','url')):
                self.assertIn(label,{item['label'] for item in fpf.completion_items(source,len(source),path,root)})
            self.assertNotIn('url',{item['label'] for item in fpf.completion_items('{{ u',4,path,root)})
            source='{% i';item=next(item for item in fpf.completion_items(source,len(source),path,root) if item['label']=='if')
            edits,caret,stops=prepare_completion(source,len(source),item)
            for start,end,value,_ in reversed(edits):source=source[:start]+value+source[end:]
            self.assertEqual(fpf.check_source(source,path),[])

    def test_literal_strings_and_unicode_positions_do_not_produce_false_completions(self):
        path=Path('/tmp/standalone.fpf')
        source="{{ default title 'fallback"
        self.assertEqual(fpf.completion_items(source,len(source),path,extra_fields=['fallbackField']),[])
        source='<a href="{{ u'
        items=fpf.completion_items(source,len(source),path,extra_fields=['user'])
        self.assertEqual([item['label'] for item in items],['url'])
        text='😀 {{ title }}'
        positions=fpf.TextPositions(text)
        self.assertEqual(positions.index(text.index('title')),'1.6')
        self.assertEqual(positions.index(len(text)),fpf.text_index(text,len(text)))

    def test_highlight_classes_and_settings_validation(self):
        source='<!-- note -->\n<a href="{{ url link }}">{% if user %}{{ title }}{% endif %}</a>'
        spans=list(code_theme.spans(source,'FusionPHP'))
        self.assertEqual({kind for kind,_,_ in spans},{'comment','tag','attribute','string','function','keyword','field'})
        self.assertEqual(code_theme.EXTENSIONS['.fpf'],'FusionPHP')
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'settings.json';path.write_text('{"indent":true,"mode":"Twig","fields":["good.name","bad()",4]}')
            settings=read_settings(path)
            self.assertEqual(settings['indent'],4);self.assertEqual(settings['mode'],'Auto');self.assertEqual(settings['fields'],['good.name'])


class FrameworkParityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('php') and Path('/home/make/Projects/FusionPHP/fusionphp/core/Template.php').is_file(),'Local FusionPHP/PHP oracle is optional')
    def test_local_compiler_acceptance_matches_without_rendering_or_bootstrapping(self):
        framework=Path('/home/make/Projects/FusionPHP');cases=[{'source':source,'layout':False} for source in GOOD+BAD]
        for path in framework.glob('app/**/*.fpf'):cases.append({'source':path.read_text(),'layout':path.parent.name=='layouts'})
        cases += [dict(source='{% yield content %}',layout=True),dict(source='{% extends "main" %}',layout=True)]
        program=r'''
require $argv[1];
$class = new ReflectionClass('Fusion\\Core\\Template');
$result = [];
foreach (json_decode(stream_get_contents(STDIN),true) as $case) {
 try {
  $tokens=$class->getMethod('tokenize')->invoke(null,'fixture.fpf',$case['source']);
  $compiler=$class->newInstanceWithoutConstructor();
  $class->getConstructor()->invoke($compiler,'fixture.fpf',$tokens);
  $class->getMethod('compile')->invoke($compiler,$case['layout']);
  $result[]=true;
 } catch (Throwable $error) { $result[]=false; }
}
echo json_encode($result);
'''
        result=subprocess.run([shutil.which('php'),'-r',program,str(framework/'fusionphp/core/Template.php')],input=json.dumps(cases),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);expected=json.loads(result.stdout)
        for case,accepted in zip(cases,expected):
            with self.subTest(source=case['source'][:80]):
                self.assertEqual(not fpf.check_source(case['source'],mode='Layout' if case['layout'] else 'Page'),accepted)


class GuiTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.folder=Path(self.temp.name)
        self.theme_file=patch.object(code_theme,'__file__',str(self.folder/'theme_module.py'));self.theme_file.start()
        self.dialog=patch.object(core.messagebox,'showerror');self.showerror=self.dialog.start()
        for owner in ('loading_indicator','code_theme','fusionphp_tools'):
            (core.PLUGIN_DIR/(owner+'.py')).write_text('from pocket_editor_plugins.'+owner+' import register\n')
        self.editor.load_plugins();self.pump(.15)
        self.app=next(callback.__self__ for label,callback in self.editor.plugin_apis['fusionphp_tools']._commands if label=='FusionPHP Tools')
        self.app.settings['automatic']=False;self.app.settings['syntax_automatic']=False
    def tearDown(self):
        self.editor.buffer.edit_modified(False);self.editor.rich_dirty=False
        try:fixtures.PaletteTests.tearDown(self)
        finally:self.dialog.stop();self.theme_file.stop()
    def pump(self,seconds=.03):
        until=time.monotonic()+seconds
        while time.monotonic()<until:self.root.update();time.sleep(.005)
    def until(self,predicate,timeout=5):
        deadline=time.monotonic()+timeout
        while not predicate() and time.monotonic()<deadline:self.pump()
        self.assertTrue(predicate(),'GUI result timed out')
    def open_source(self,source):
        path=self.folder/'page.fpf';path.write_text(source);self.editor.open_path(path);self.pump()
        self.app.cancel_timer('pending_check');return path
    def show(self):
        self.app.show();self.until(lambda:self.app.window is not None and 'fusionphp_tools' not in self.editor.plugin_sidebar.pending)
    def test_reopen_uses_one_docked_panel_and_save_settings_stays_visible(self):
        self.open_source('<p>{{ title }}</p>');self.show();panel=self.app.window
        self.assertTrue(getattr(panel,'_docked_panel',False))
        self.editor.plugin_sidebar.hide();self.app.show();self.pump()
        self.assertIs(panel,self.app.window);self.assertTrue(panel.winfo_ismapped())
        self.app.preferences();self.app.settings_window.geometry('360x320');self.root.update()
        from test_editor import widgets
        save=next(widget for widget in widgets(self.app.settings_window) if isinstance(widget,ttk.Button) and widget.cget('text')=='Save Settings')
        self.assertTrue(save.winfo_ismapped())
        self.assertLessEqual(save.winfo_rooty()+save.winfo_height(),self.app.settings_window.winfo_rooty()+self.app.settings_window.winfo_height())
        save.invoke();self.assertTrue(self.app.settings_path.is_file())
    def test_real_syntax_error_has_red_zigzag_and_clears_after_edit(self):
        path=self.open_source('<p>{{ title }}</p>\n{% bad %}')
        self.show();self.app.check();self.until(lambda:not self.app.jobs);self.root.update();self.app.marks.draw()
        self.assertTrue(self.app.diagnostics);self.assertTrue(self.app.issues.get_children());self.assertTrue(self.app.marks.canvases)
        canvas=self.app.marks.canvases[0];line=canvas.find_all()[0]
        self.assertEqual(canvas.itemcget(line,'fill'),'#ff5252');self.assertEqual(set(canvas.coords(line)[1::2]),{1.,3.})
        self.app.jump(self.app.diagnostics[0]);self.assertEqual(self.editor.buffer.index('insert'),'2.0')
        self.editor.buffer.insert('end-1c','\n');self.pump(.15);self.assertFalse(self.app.marks.canvases)
        self.assertEqual(path.read_text(),'<p>{{ title }}</p>\n{% bad %}')
    def test_format_is_undoable_does_not_save_and_discards_stale_work(self):
        source='<section>\n<p>{{ title }}</p>\n</section>\n';path=self.open_source(source)
        self.app.format_document();self.until(lambda:not self.app.jobs)
        self.assertIn('    <p>',self.app.api.get_text());self.assertEqual(path.read_text(),source)
        self.editor.buffer.edit_undo();self.assertEqual(self.app.api.get_text(),source)
        started=threading.Event();release=threading.Event();original=fpf.format_source
        def work(*args):started.set();release.wait(2);return original(*args)
        with patch.object(fpf,'format_source',side_effect=work):
            self.app.format_document();self.until(started.is_set)
            self.editor.buffer.insert('end-1c','newer');release.set();self.until(lambda:not self.app.jobs)
        self.assertIn('newer',self.app.api.get_text());self.assertNotIn('    <p>',self.app.api.get_text())
    def test_completion_snippet_and_live_theme_highlighting(self):
        self.open_source('{% i');self.editor.buffer.mark_set('insert','end-1c')
        self.app.complete();self.until(lambda:not self.app.jobs)
        self.assertTrue(self.app.popup.winfo_viewable());self.app.accept()
        self.assertIn('{% if condition %}',self.app.api.get_text());self.assertIn('{% endif %}',self.app.api.get_text())
        self.editor.buffer.edit_undo();self.assertEqual(self.app.api.get_text(),'{% i')
        self.editor.buffer.delete('1.0','end');self.editor.buffer.insert('1.0','<p>{{ trim title }}</p>')
        self.until(lambda:bool(self.editor.buffer.tag_ranges('syntax_tag')))
        self.assertTrue(self.editor.buffer.tag_ranges('syntax_function'));self.assertTrue(self.editor.buffer.tag_ranges('syntax_field'))
        self.app.theme();self.root.update()
        self.assertTrue(any(window.winfo_class()=='Toplevel' and window.title()=='Code Theme' for window in self.root.winfo_children()))
    def test_unicode_highlighting_and_error_marks_follow_font_and_wrapping(self):
        self.open_source('<p>😀 {{ title }}</p>')
        self.until(lambda:bool(self.editor.buffer.tag_ranges('syntax_field')))
        self.assertEqual(self.editor.buffer.get('syntax_field.first','syntax_field.last'),'title')
        self.editor.buffer.delete('1.0','end');self.editor.buffer.insert('1.0','<p>😀 {{ user() }}</p>')
        self.app.check();self.until(lambda:not self.app.jobs)
        self.assertTrue(self.app.marks.canvases)
        self.app.jump(self.app.diagnostics[0]);self.assertEqual(self.editor.buffer.get('insert','insert +2c'),'{{')
        signature=self.app.marks.signature
        self.editor.editor_font.configure(size=18);self.editor.buffer.configure(wrap='word');self.root.update();self.app.marks.draw()
        self.assertNotEqual(signature,self.app.marks.signature)

    def test_own_colors_work_without_code_theme_and_override_restored_white_tags(self):
        # Simulate the installed release's old highlighter or disabled Code Theme.
        api=self.editor.plugin_apis.pop('code_theme')
        try:
            self.open_source('<a href="{{ url link }}">{% if user %}{{ title }}{% endif %}</a>')
            self.editor.buffer.tag_configure('rich_white',foreground='#ffffff')
            self.editor.buffer.tag_add('rich_white','1.0','end')
            self.app.highlight_signature=None;self.app.paint_highlighting()
            for kind in ('tag','attribute','function','keyword','field'):
                name='fpf_syntax_'+kind
                self.assertTrue(self.editor.buffer.tag_ranges(name),kind)
                self.assertNotEqual(self.editor.buffer.tag_cget(name,'foreground'),self.editor.buffer.cget('foreground'))
                names=list(self.editor.buffer.tag_names())
                self.assertGreater(names.index(name),names.index('rich_white'))
            self.assertEqual(self.editor.buffer.tag_cget('fpf_syntax_field','foreground'),'#9cdcfe')
            self.editor.buffer.configure(background='#ffffff',foreground='#24292f')
            self.app.paint_highlighting()
            self.assertEqual(self.editor.buffer.tag_cget('fpf_syntax_field','foreground'),'#0550ae')
            self.app.settings['highlighting']=False;self.app.paint_highlighting()
            self.assertFalse(self.editor.buffer.tag_ranges('fpf_syntax_field'))
        finally:self.editor.plugin_apis['code_theme']=api

    def test_colors_are_present_with_plain_text_language_and_survive_tab_switches(self):
        self.app.theme();self.root.update()
        dialog=next(window for window in self.root.winfo_children() if window.winfo_class()=='Toplevel' and window.title()=='Code Theme')
        from test_editor import widgets
        combo=next(widget for widget in widgets(dialog) if isinstance(widget,ttk.Combobox) and 'Plain text' in widget.cget('values'))
        combo.set('Plain text');combo.event_generate('<<ComboboxSelected>>');dialog.destroy()
        path=self.open_source('<h1>{{ title }}</h1>')
        self.until(lambda:bool(self.editor.buffer.tag_ranges('fpf_syntax_tag')))
        self.assertFalse(self.editor.buffer.tag_ranges('syntax_tag'))
        self.assertEqual(self.editor.buffer.get('fpf_syntax_field.first','fpf_syntax_field.last'),'title')
        other=self.folder/'other.txt';other.write_text('Plain content')
        self.editor.open_path(other);self.pump(.15)
        self.assertFalse(self.editor.buffer.tag_ranges('fpf_syntax_tag'))
        self.editor.open_path(path);self.pump(.15)
        self.assertTrue(self.editor.buffer.tag_ranges('fpf_syntax_tag'))

    def test_invalid_format_keeps_file_and_loading_waits_for_rendering(self):
        source='{% if flag %}';path=self.open_source(source)
        self.app.format_document();self.until(lambda:not self.app.jobs)
        self.assertEqual(self.app.api.get_text(),source);self.assertEqual(path.read_text(),source);self.showerror.assert_called()
        self.until(lambda:not self.app.api.call_service('loading_indicator.status'))
        self.assertTrue(self.app.diagnostics)

