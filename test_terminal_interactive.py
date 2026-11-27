"""Real PTY, screen emulation and integrated terminal menu regression checks."""
import os
import queue
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import pocket_editor as core
import test_command_palette as fixtures
from pocket_editor_plugins import project_terminal
from pocket_editor_plugins._terminal_session import TerminalSession,engine,make_screen

try:pyte,_transport=engine();READY=True
except RuntimeError:READY=False


@unittest.skipUnless(READY and os.name!='nt','Requires optional POSIX terminal engine')
class PtyTests(unittest.TestCase):
    def setUp(self):
        self.session=TerminalSession();self.session.start('/tmp','/bin/sh');self.output=''
        self.wait(lambda:'started' in self.events)
    def tearDown(self):self.session.close()
    def wait(self,predicate,timeout=8):
        if not hasattr(self,'events'):self.events=[]
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:
            try:kind,value=self.session.messages.get(timeout=.1)
            except queue.Empty:continue
            self.events.append(kind)
            if kind=='data':self.output+=value
            if kind=='error':self.fail(value)
        self.assertTrue(predicate(),repr(self.output[-2000:]))
    def test_tty_persistent_directory_and_interactive_input(self):
        self.session.write("test -t 0 && test -t 1 && test -t 2 && printf '__TTY_YES__\\n'\r")
        self.wait(lambda:'__TTY_YES__\r\n' in self.output)
        self.session.write("cd /; printf '__PWD_%s__\\n' \"$PWD\"\r")
        self.wait(lambda:'__PWD_/__\r\n' in self.output)
        self.session.write("read value; printf '__INPUT_%s__\\n' \"$value\"\r")
        self.session.write('pocket\r');self.wait(lambda:'__INPUT_pocket__\r\n' in self.output)
    def test_ctrl_c_interrupt_and_real_resize(self):
        self.session.write('sleep 60\r');time.sleep(.1);self.session.write('\x03')
        self.session.write("printf '__INTERRUPT_OK__\\n'\r");self.wait(lambda:'__INTERRUPT_OK__\r\n' in self.output)
        self.session.resize(31,97);self.session.write("stty size; printf '__SIZE_DONE__\\n'\r")
        self.wait(lambda:'31 97' in self.output)
    def test_stop_reaps_foreground_job(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'pid'
            script=f'import os,time;open({str(marker)!r},"w").write(str(os.getpid()));time.sleep(60)'
            import shlex
            self.session.write(shlex.quote(sys.executable)+' -c '+shlex.quote(script)+'\r')
            end=time.monotonic()+5
            while not marker.exists() and time.monotonic()<end:time.sleep(.02)
            self.assertTrue(marker.exists());pid=int(marker.read_text());self.session.close()
            stat=Path(f'/proc/{pid}/stat')
            if Path('/proc').exists():self.assertTrue(not stat.exists() or stat.read_text().split()[2]=='Z')


@unittest.skipUnless(READY,'Requires optional terminal engine')
class ScreenTests(unittest.TestCase):
    def test_cursor_rewrite_colors_and_alternate_buffer(self):
        screen=make_screen(pyte,40,8,lambda value:None);stream=pyte.Stream(screen)
        stream.feed('hello\rWORLD');self.assertTrue(screen.display[0].startswith('WORLD'))
        stream.feed('\x1b[31mRED\x1b[0m');self.assertEqual(screen.buffer[0][5].fg,'red')
        saved=list(screen.display);stream.feed('\x1b[?1049hALT');self.assertTrue(screen.display[0].startswith('ALT'))
        stream.feed('\x1b[?1049l');self.assertEqual(screen.display,saved)
        stream.feed('\x1b[2J\x1b[H');self.assertEqual(screen.display[0].strip(),'')


class TerminalMenuTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        self.settings=patch.object(project_terminal,'SETTINGS_PATH',core.PLUGIN_DIR/'terminal.json');self.settings.start()
        self.api=core.PluginAPI(self.editor,'project_terminal');self.editor.plugin_apis['project_terminal']=self.api;project_terminal.register(self.api)
        self.api._services['show']();self.until(lambda:'project_terminal' in self.editor.plugin_sidebar.hosts and 'project_terminal' not in self.editor.plugin_sidebar.pending)
        self.panel=self.editor.plugin_sidebar.hosts['project_terminal']._panel_widget
    def tearDown(self):
        try:fixtures.PaletteTests.tearDown(self)
        finally:self.settings.stop()
    def until(self,predicate,timeout=8):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertTrue(predicate())
    def all(self,parent):
        for child in parent.winfo_children():yield child;yield from self.all(child)
    def test_one_menu_bar_and_preserved_settings_and_runner(self):
        buttons=[w for w in self.all(self.panel) if isinstance(w,ttk.Menubutton)]
        self.assertEqual([w.cget('text') for w in buttons],['Terminal','Edit','Project','View','Settings'])
        self.assertEqual(len({w.master for w in buttons}),1)
        self.assertFalse(any(isinstance(w,ttk.Entry) for w in self.all(self.panel)))
        self.assertFalse(any(isinstance(w,ttk.Button) for w in self.all(self.panel)))
        self.api._services['run_command'](f'"{sys.executable}" -c "print(12345)"',str(core.PLUGIN_DIR))
        output=next(w for w in self.all(self.panel) if getattr(w,'_terminal_role','')=='runner')
        self.until(lambda:'[Exit code 0]' in output.get('1.0','end'))
        self.assertIn('12345',output.get('1.0','end'))
        self.assertTrue(self.api._services['get_settings']()['ansi'])
        terminal=self.root.nametowidget(buttons[0].cget('menu'))
        terminal.invoke('Close Terminal');self.root.update()
        self.assertFalse(self.editor.plugin_sidebar.visible)
        self.api._services['show']();self.root.update()
        self.assertTrue(self.panel.winfo_exists());self.assertTrue(self.editor.plugin_sidebar.visible)
    @unittest.skipUnless(READY,'Requires optional terminal engine')
    def test_keyboard_input_and_shutdown(self):
        shell=next(w for w in self.all(self.panel) if getattr(w,'_terminal_role','')=='shell')
        self.until(lambda:bool(self.api._services['send_input']('')))
        from pocket_editor_plugins._terminal_session import shell_command
        program=Path(shell_command()[0]).stem.lower()
        prefix='& ' if os.name=='nt' and program in ('pwsh','powershell') else ''
        self.api._services['send_input'](prefix+f'"{sys.executable}" -c "print(\'__KEY_\'+\'TEST__\')"\r')
        self.until(lambda:any(line.strip()=='__KEY_TEST__' for line in shell.get('1.0','end').splitlines()))
        self.assertTrue(shell.bind('<KeyPress>'));self.assertTrue(shell.bind('<<Paste>>'))
        self.api._services['stop_shell']();self.assertFalse(self.api._services['send_input']('echo nope\r'))


@unittest.skipUnless(READY,'Requires optional terminal engine')
class TerminalSelectionTests(unittest.TestCase):
    def setUp(self):
        fixtures.PaletteTests.setUp(self)
        from pocket_editor_plugins._terminal_view import InteractiveTerminal
        from pocket_editor_plugins._terminal_colors import validate_settings
        self.view=InteractiveTerminal(self.root,self.root,tk.StringVar(self.root),validate_settings({}),lambda:'')
        self.view.frame.pack(fill='both',expand=True);self.root.update()
        self.view.screen=make_screen(pyte,50,8,lambda value:None);self.view.stream=pyte.Stream(self.view.screen)
        self.view.stream.feed('\x1b[31mRED TEXT\x1b[0m normal');self.view.draw()
    def tearDown(self):
        self.view.cleanup();fixtures.PaletteTests.tearDown(self)
    def test_selection_above_ansi_and_visible_when_unfocused(self):
        text=self.view.text;text.tag_add('sel','1.0','1.3')
        tags=text.tag_names('1.1');self.assertEqual(tags[-1],'sel')
        self.assertEqual(text.cget('inactiveselectbackground'),text.cget('selectbackground'))
        self.assertEqual(text.cget('selectforeground'),'#ffffff')
        text.focus_force();self.root.update();self.editor.buffer.focus_force();self.root.update()
        self.assertEqual(text.get('sel.first','sel.last'),'RED')
    def test_selection_snapshot_stays_stable_during_live_output(self):
        text=self.view.text;text.tag_add('sel','1.0','1.3');before=text.get('1.0','end')
        self.view.stream.feed('\rCHANGED');self.view.draw()
        self.assertEqual(text.get('1.0','end'),before);self.assertTrue(self.view.redraw_pending)
        self.view.copy();self.assertEqual(self.root.clipboard_get(),'RED')
        self.view.clear_selection();self.assertTrue(text.get('1.0','end').startswith('CHANGED'))
        self.assertFalse(self.view.redraw_pending)
    def test_popup_anchored_on_release_and_defers_redraw_until_unmap(self):
        text=self.view.text
        self.assertTrue(text.bind('<ButtonRelease-3>'))
        self.assertTrue(text.bind('<B3-Motion>'))
        event=SimpleNamespace(x_root=123,y_root=234)
        with patch.object(tk.Menu,'tk_popup') as post:
            self.view.context(event);menu=self.view.popup_menu
            post.assert_called_once_with(125,236)
            self.view.context(event);post.assert_called_once()
        before=text.get('1.0','end');self.view.stream.feed('\rNEW OUTPUT');self.view.draw()
        self.assertEqual(text.get('1.0','end'),before)
        self.view.menu_closed();self.root.update()
        self.assertTrue(text.get('1.0','end').startswith('NEW OUTPUT'))
        self.assertIsNone(self.root.grab_current())
        with patch.object(tk.Menu,'tk_popup'):
            self.view.context(event);self.assertIs(self.view.popup_menu,menu)
        self.view.menu_closed()
    def test_selection_drag_defers_render_and_clear_removes_selection(self):
        text=self.view.text;self.view.selection_start()
        before=text.get('1.0','end');self.view.stream.feed('\rDRAG OUTPUT');self.view.draw()
        self.assertEqual(text.get('1.0','end'),before)
        self.view.selection_end();self.root.update()
        self.assertTrue(text.get('1.0','end').startswith('DRAG OUTPUT'))
        self.view.select_all();self.assertTrue(text.tag_ranges('sel'))
        self.view.clear();self.assertFalse(text.tag_ranges('sel'))
