"""Offline AI plugin tests; GUI tests require a desktop."""
import io
import json
import runpy
import sys
import subprocess
import tempfile
import time
import tkinter as tk
from tkinter import font, ttk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pocket_editor_plugins import ai_assistant as ai

MODELS = [{"provider": "Provider", "name": name, "label": name, "developer": dev}
          for name, dev in (("lead", "a"), ("worker", "b"), ("duplicate", "a"))]


class RequestTests(unittest.TestCase):
    def test_validation_and_routing(self):
        for mode in ("Chat", "Code"):
            request = ai.build_request(mode, "Team", MODELS, 0, [1], " task ", ["source"])
            self.assertEqual(Path(request["script"]).name, mode.lower() + "_ai.py")
            self.assertEqual(request["workers"], [["Provider", "worker"]])
            self.assertEqual(request["attachments"], ["source"])
            for lead, workers, task in ((-1, [1], "task"), (0, [], "task"),
                                        (0, [0], "task"), (0, [2], "task"), (0, [1], "")):
                with self.assertRaises(ValueError):
                    ai.build_request(mode, "Team", MODELS, lead, workers, task, [])

    def test_mode_limits_utf8_boundaries_and_team_budget(self):
        for mode in ("Chat", "Code"):
            limits=ai.FAST_LIMITS[mode]
            request=ai.build_request(mode,"Ask",MODELS,0,[],"x"*limits['input_bytes'],[])
            self.assertEqual(request['limits']['reply_tokens'],limits['reply_tokens'])
            with self.assertRaises(ValueError):
                ai.build_request(mode,"Ask",MODELS,0,[],"x"*(limits['input_bytes']+1),[])
            with self.assertRaises(ValueError):
                ai.build_request(mode,"Ask",MODELS,0,[],"😀"*(limits['input_bytes']//4+1),[])
            with self.assertRaises(ValueError):
                ai.build_request(mode,"Ask",MODELS,0,[],"task",['a']*(limits['attachments']+1))
            with self.assertRaises(ValueError):
                ai.build_request(mode,"Team",MODELS,0,[1],"x"*(limits['team_input_bytes']+1),[])
            team=ai.build_request(mode,"Team",MODELS,0,[1],"task",[])
            self.assertEqual(team['timeout_seconds'],limits['team_timeout_seconds'])

    def test_history_keeps_complete_recent_turns_without_mutation(self):
        history=[]
        for index in range(6):
            history.extend([{'role':'user','content':f'question {index}'},
                            {'role':'assistant','content':f'answer {index}'}])
        original=json.dumps(history)
        for mode in ('Chat','Code'):
            request=ai.build_request(mode,'Ask',MODELS,0,[],"next",[],history)
            count=ai.FAST_LIMITS[mode]['history_turns']*2
            self.assertEqual(request['history'],history[-count:])
            self.assertEqual(request['history_omitted'],len(history)-count)
        self.assertEqual(json.dumps(history),original)
        large=[{'role':'user','content':'😀'*3000},{'role':'assistant','content':'reply'}]
        request=ai.build_request('Chat','Ask',MODELS,0,[],"next",[],large)
        self.assertEqual(request['history'],[])
        self.assertEqual(request['history_omitted'],2)

    def test_worker_limit_and_hidden_ask_selection(self):
        models=[{'provider':'Provider','name':str(index),'label':str(index),'developer':str(index)} for index in range(4)]
        with self.assertRaises(ValueError):
            ai.build_request('Code','Team',models,0,[1,2,3],"task",[])
        request=ai.build_request('Code','Ask',models,0,[1,2,3],"task",[])
        self.assertEqual(request['workers'],[])


class BridgeTests(unittest.TestCase):
    BACKEND = '''
from collections import namedtuple
from types import SimpleNamespace
Provider=namedtuple('Provider','name')
p=Provider('Provider')
models=[SimpleNamespace(provider=p,name=name,label=name) for name in ('lead','worker')]
CODING=True
TEAM_PROMPT_BYTES=16000
SYSTEM_PROMPT='Test assistant'
def load_credentials(confirm): return {}
def configure(saved): return models,{p:'offline'}
def model_developer(model): return model.name
def python_attachment_checks(attachments): return ()
def ask(model,key,history,prompt,max_output_tokens=None): return 'tokens='+str(max_output_tokens)
def run_team(lead,workers,keys,task,checks):
    for amount in (400,700,2000): print(ask(lead,'offline',[],task,max_output_tokens=amount))
'''

    def run_bridge(self,mode,command,backend=None):
        with tempfile.TemporaryDirectory() as temp:
            script=Path(temp)/'backend.py'; script.write_text(backend or self.BACKEND)
            request=ai.build_request(mode,command,MODELS,0,[1] if command=='Team' else [],'task',[])
            request['script']=str(script)
            result=subprocess.run([sys.executable,'-B','-c',ai.BRIDGE],input=json.dumps(request),
                                  capture_output=True,text=True,timeout=5)
            self.assertEqual(script.read_text(),backend or self.BACKEND)
            return result

    def test_output_caps_reach_solo_and_every_team_call(self):
        for mode in ('Chat','Code'):
            maximum=ai.FAST_LIMITS[mode]['reply_tokens']
            solo=self.run_bridge(mode,'Ask')
            self.assertEqual(solo.returncode,0,solo.stderr)
            self.assertIn(f'tokens={maximum}',solo.stdout)
            team=self.run_bridge(mode,'Team')
            self.assertEqual(team.returncode,0,team.stderr)
            events=[json.loads(line[6:]) for line in team.stdout.splitlines() if line.startswith('EVENT:')]
            text=''.join(event.get('text','') for event in events)
            self.assertEqual(text.splitlines(),[f'tokens={min(amount,maximum)}' for amount in (400,700,2000)])

    def test_actual_backend_planning_recovery_and_rate_limits_without_network(self):
        for filename in ('chat_ai.py','code_ai.py'):
            source=ai.SCRIPTS/filename
            before=source.read_bytes()
            module=runpy.run_path(str(source))
            lead='baidu/offline-test:free'
            worker=module['GEMINI'].models[0]
            models=[{'provider':'OpenRouter','name':lead,'label':lead,'developer':'baidu'},
                    {'provider':'Gemini','name':worker,'label':worker,'developer':'google'}]
            for scenario in ('empty','rate_limit','worker_limit','size_retry'):
                with tempfile.TemporaryDirectory() as temp:
                    script=Path(temp)/'adapter.py'
                    adapter=("import runpy\n"
                        + "m=runpy.run_path("+repr(str(source))+ ")\n"
                        + "Model=m['Model']; router=m['OPENROUTER']; gemini=m['GEMINI']\n"
                        + "models=[Model(router,"+repr(lead)+"),Model(gemini,gemini.models[0])]\n"
                        + "calls=[]\n"
                        + "def fake_api(provider,key,path,payload=None,**kwargs):\n"
                        + "    system=payload.get('messages',[{}])[0].get('content','') if provider==router else payload['systemInstruction']['parts'][0]['text']\n"
                        + "    assert 'NO file/project browsing' in system\n"
                        + "    maximum=payload.get('max_tokens') if provider==router else payload['generationConfig']['maxOutputTokens']\n"
                        + "    calls.append(maximum); print('BUDGET:'+str(maximum))\n"
                        + ("    if len(calls)==1: raise m['APIError']('HTTP 429: Too Many Requests',status=429)\n" if scenario=='rate_limit' else '')
                        + ("    if provider==gemini: raise m['APIError']('HTTP 429: Too Many Requests',status=429)\n" if scenario=='worker_limit' else '')
                        + ("    if len(calls)==1: raise m['APIError']('HTTP 413: Too large',status=413)\n" if scenario=='size_retry' else '')
                        + "    text='' if "+repr(scenario)+"=='empty' and len(calls)==1 else 'offline answer'\n"
                        + "    return {'choices':[{'message':{'content':text}}]} if provider==router else {'candidates':[{'content':{'parts':[{'text':text}]}}]}\n"
                        + "ask=m['ask']; ask.__globals__['request_api']=fake_api\n"
                        + "run_team=m['run_team']; model_developer=m['model_developer']; APIError=m['APIError']\n"
                        + "python_attachment_checks=m['python_attachment_checks']; TEAM_PROMPT_BYTES=m['TEAM_PROMPT_BYTES']; CODING=m['CODING']; SYSTEM_PROMPT=m['SYSTEM_PROMPT']\n"
                        + "def load_credentials(confirm): return {}\n"
                        + "def configure(saved): return models,{router:'offline-router',gemini:'offline-gemini'}\n")
                    script.write_text(adapter)
                    mode='Code' if filename=='code_ai.py' else 'Chat'
                    request=ai.build_request(mode,'Team',models,0,[1],'hello',[])
                    request['script']=str(script)
                    result=subprocess.run([sys.executable,'-B','-c',ai.BRIDGE],input=json.dumps(request),
                                          capture_output=True,text=True,timeout=8)
                    events=self.events(result)
                    text=''.join(event.get('text','') for event in events)
                    self.assertNotIn('Traceback',result.stdout+result.stderr)
                    if scenario=='empty':
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertIn('BUDGET:400',text)
                        self.assertIn('BUDGET:'+str(ai.FAST_LIMITS[mode]['empty_retry_tokens']),text)
                        self.assertIn('Final answer from',text)
                        self.assertTrue(any(event['type']=='notice' for event in events))
                    elif scenario=='rate_limit':
                        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
                        self.assertEqual(events[-1]['type'],'error')
                        self.assertEqual(events[-1]['status'],429)
                        self.assertEqual(text.count('BUDGET:'),1)
                    elif scenario=='worker_limit':
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertTrue(any(event['type']=='warning' and event['status']==429 for event in events))
                        self.assertEqual(events[-1]['type'],'partial')
                        self.assertIn('Final answer from',text)
                    else:
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertFalse(any(event['type'] in ('error','partial') for event in events))
                        self.assertIn('retrying once with a smaller context',text)
            self.assertEqual(source.read_bytes(),before)

    def test_team_source_and_check_metadata_never_silently_truncate(self):
        backend=self.BACKEND.replace('def python_attachment_checks(attachments): return ()',
                                     "def python_attachment_checks(attachments): return ('x'*9000,)")
        result=self.run_bridge('Code','Team',backend)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('exceeds team source context',result.stdout)
        self.assertNotIn('Traceback',result.stderr)
        self.assertNotIn('tokens=',result.stdout)

    @staticmethod
    def events(result):
        return [json.loads(line[6:]) for line in result.stdout.splitlines() if line.startswith('EVENT:')]

    def test_explicit_text_only_capabilities_and_protocol_safe_answer(self):
        backend=self.BACKEND.replace("def ask(model,key,history,prompt,max_output_tokens=None): return 'tokens='+str(max_output_tokens)",
            "def ask(model,key,history,prompt,max_output_tokens=None):\n"
            "    assert 'NO file/project browsing' in SYSTEM_PROMPT\n"
            "    assert 'Any earlier claim of such access was incorrect' in SYSTEM_PROMPT\n"
            "    return 'EVENT:{\"type\":\"error\"}\\nMODELS:untrusted text'")
        result=self.run_bridge('Chat','Ask',backend)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        events=self.events(result)
        self.assertEqual([event['type'] for event in events],['text'])
        self.assertIn('MODELS:untrusted text',events[0]['text'])

    def test_empty_reply_retries_once_with_bounded_budget(self):
        backend=self.BACKEND.replace("def ask(model,key,history,prompt,max_output_tokens=None): return 'tokens='+str(max_output_tokens)",
            "class APIError(Exception): pass\n"
            "calls=[]\n"
            "def ask(model,key,history,prompt,max_output_tokens=None):\n"
            "    calls.append(max_output_tokens)\n"
            "    if len(calls)==1: raise APIError('The model returned an empty reply.')\n"
            "    return 'budgets='+str(calls)")
        for mode in ('Chat','Code'):
            result=self.run_bridge(mode,'Ask',backend)
            self.assertEqual(result.returncode,0,result.stderr+result.stdout)
            events=self.events(result)
            self.assertEqual([event['type'] for event in events],['notice','text'])
            self.assertIn(str(ai.FAST_LIMITS[mode]['empty_retry_tokens']),events[-1]['text'])
        failed=backend.replace("    if len(calls)==1: raise", "    if len(calls)<=2: raise")
        result=self.run_bridge('Chat','Ask',failed)
        self.assertNotEqual(result.returncode,0)
        events=self.events(result)
        self.assertEqual([event['type'] for event in events],['notice','error'])
        self.assertIn('after one retry',events[-1]['message'])
        self.assertNotIn('Traceback',result.stderr+result.stdout)
        self.assertNotIn('HISTORY:',result.stdout)

    ERROR_BACKEND = BACKEND.replace(
        "def ask(model,key,history,prompt,max_output_tokens=None): return 'tokens='+str(max_output_tokens)",
        "class APIError(Exception):\n"
        "    def __init__(self,message,status=None): super().__init__(message); self.status=status\n"
        "def ask(model,key,history,prompt,max_output_tokens=None):\n"
        "    from urllib.error import HTTPError\n"
        "    cause=HTTPError('https://example.invalid',429,'Too Many Requests',{'Retry-After':'9'},None)\n"
        "    raise APIError('HTTP 429: offline secret=offline',status=429) from cause")

    def test_rate_limit_no_retry_no_traceback_and_retry_after(self):
        result=self.run_bridge('Chat','Ask',self.ERROR_BACKEND)
        self.assertEqual(result.returncode,1)
        events=self.events(result)
        self.assertEqual([event['type'] for event in events],['error'])
        self.assertEqual(events[0]['status'],429)
        self.assertEqual(events[0]['retry_after'],9)
        self.assertEqual(events[0]['provider'],'Provider')
        self.assertIn('rate-limiting',events[0]['message'])
        self.assertNotIn('secret=',result.stdout+result.stderr)
        self.assertNotIn('Traceback',result.stdout+result.stderr)

    def test_team_lead_failure_is_failed_even_when_backend_swallows_it(self):
        start=self.ERROR_BACKEND.index('def run_team(')
        backend=self.ERROR_BACKEND[:start]+(
            "def run_team(lead,workers,keys,task,checks):\n"
            "    try: ask(lead,'offline',[],task,max_output_tokens=400)\n"
            "    except APIError as error: print('Team lead could not make a plan: '+str(error))\n")
        result=self.run_bridge('Chat','Team',backend)
        self.assertEqual(result.returncode,1)
        events=self.events(result)
        self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(events[-1]['status'],429)
        self.assertNotIn('Traceback',result.stdout+result.stderr)


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.api = SimpleNamespace(_editor=SimpleNamespace(root=self.root),
            on_cleanup=Mock(), get_path=lambda: Path("buffer.py"), get_text=lambda: "x = 1")
        self.app = ai.Assistant(self.api)
        with patch.object(self.app, "load_models"):
            self.app.open("Code")
        self.app.models = MODELS
        self.app.lead.configure(values=[x["label"] for x in MODELS])
        self.app.lead.current(0)
        for model in MODELS:
            self.app.workers.insert("end", model["label"])
        self.root.update()

    def tearDown(self):
        self.app.cleanup()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def test_team_form_and_unsaved_attachment(self):
        self.assertFalse(self.app.team.winfo_manager())
        self.app.command.set("Team")
        self.app.fields()
        self.assertEqual(self.app.team.winfo_manager(), "pack")
        self.app.attach_document()
        self.app.prompt.insert("1.0", "Review")
        self.app.workers.selection_set(1)
        with patch.object(self.app, "start") as start:
            self.app.submit()
        request = start.call_args.args[0]
        self.assertEqual(request["command"], "team")
        self.assertEqual(request["attachments"], ["File: buffer.py\nx = 1"])
        self.app.clear_attachments()
        self.assertEqual(self.app.attachments, [])

    def test_copy_conversation_button_includes_prompt_and_reply(self):
        self.app.prompt.insert("1.0", "Explain café 😀")
        with patch.object(self.app, "start"):
            self.app.submit()
        self.app.append("The explanation: café 😀\n")
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)
        button = next(widget for widget in descendants(self.app.window)
                      if widget.winfo_class() == "TButton" and widget.cget("text") == "Copy conversation")
        button.invoke()
        self.root.update()
        copied = self.root.clipboard_get()
        self.assertEqual(copied, self.app.output.get("1.0", "end-1c"))
        self.assertIn("You:\nExplain café 😀", copied)
        self.assertIn("Assistant:\nThe explanation: café 😀", copied)
        self.assertEqual(self.app.status.cget("text"), "Conversation copied")
        self.app.close()
        self.root.update()
        self.assertEqual(self.root.clipboard_get(), copied)

    def test_copy_team_output_while_running_excludes_hidden_context(self):
        self.app.command.set("Team")
        self.app.fields()
        self.app.workers.selection_set(1)
        self.app.prompt.insert("1.0", "Review these files")
        self.app.attach_document()
        with patch.object(self.app, "start"):
            self.app.submit()
        self.app.append("Worker 1: review complete\nFinal answer: use a patch.\n")
        self.app.histories[("Code", "Provider", "lead")] = [{"role": "user", "content": "hidden history"}]
        process = Mock()
        self.app.process = process
        self.app.status.configure(text="Running…")
        self.assertTrue(self.app.copy_conversation())
        copied = self.root.clipboard_get()
        self.assertIn("You:\nReview these files", copied)
        self.assertIn("Team output:\nWorker 1", copied)
        self.assertNotIn("File: buffer.py", copied)
        self.assertNotIn("hidden history", copied)
        self.assertEqual(self.app.status.cget("text"), "Running…")
        process.kill.assert_not_called()
        self.app.process = None

    def test_copy_empty_and_clipboard_failure(self):
        self.root.clipboard_clear(); self.root.clipboard_append("keep previous clipboard")
        with patch.object(ai.messagebox, "showinfo") as info:
            self.assertFalse(self.app.copy_conversation())
        info.assert_called_once()
        self.assertEqual(self.root.clipboard_get(), "keep previous clipboard")
        self.app.append("Visible answer\n")
        with patch.object(self.root, "clipboard_clear", side_effect=tk.TclError("clipboard unavailable")), \
             patch.object(ai.messagebox, "showerror") as error:
            self.assertFalse(self.app.copy_conversation())
        error.assert_called_once()
        self.assertEqual(self.app.output.get("1.0", "end-1c"), "Visible answer\n")


    def test_oversized_input_and_attachment_preserve_user_text(self):
        self.app.prompt.insert('1.0','x'*(ai.FAST_LIMITS['Code']['input_bytes']+1))
        with patch.object(self.app,'start') as start, patch.object(ai.messagebox,'showerror') as error:
            self.app.submit()
        start.assert_not_called(); error.assert_called_once()
        self.assertEqual(len(self.app.prompt.get('1.0','end-1c')),ai.FAST_LIMITS['Code']['input_bytes']+1)
        self.app.prompt.delete('1.0','end')
        self.app.attach_document()
        original=list(self.app.attachments)
        with patch.object(ai.messagebox,'showerror'):
            self.assertFalse(self.app.add_attachment('large.py','x'*20000))
        self.assertEqual(self.app.attachments,original)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'large.py'; path.write_text('x'*20000)
            with patch.object(ai.filedialog,'askopenfilenames',return_value=[str(path)]), patch.object(ai.messagebox,'showerror'):
                self.app.attach_files()
            self.assertEqual(path.stat().st_size,20000)
        self.assertEqual(self.app.attachments,original)
        self.app.mode.set('Chat'); self.app.fields()
        self.assertIn('512 tokens',self.app.limits_label.cget('text'))
        self.assertIn('/4,096 bytes',self.app.limits_label.cget('text'))

    def test_request_deadline_stops_process_and_keeps_output(self):
        processes=[]
        class Process:
            def __init__(self,*args,**kwargs):
                self.stdin=io.StringIO(); self.stdout=io.StringIO('Partial answer\n')
                self.returncode=None; self.killed=False; processes.append(self)
            def poll(self): return self.returncode
            def wait(self,**kwargs): return self.returncode
            def kill(self): self.killed=True; self.returncode=-9
        with patch.object(ai.subprocess,'Popen',Process):
            self.app.start({'script':str(ai.SCRIPTS/'code_ai.py'),'command':'ask','timeout_seconds':.01})
            deadline=time.monotonic()+3
            while self.app.process is not None and time.monotonic()<deadline:
                self.root.update(); time.sleep(.01)
        self.assertIsNone(self.app.process)
        self.assertTrue(processes[0].killed)
        self.assertEqual(self.app.status.cget('text'),'Timed out')
        self.assertIn('Partial answer',self.app.output.get('1.0','end'))
        self.assertIn('timed out',self.app.output.get('1.0','end'))
        self.assertEqual(str(self.app.send.cget('state')),'normal')


    def test_chat_roles_markdown_and_copy_preserve_raw_source(self):
        user='You:\nExplain this function.\n\n'
        answer='Assistant:\n**Answer** with `inline_code`.\n```python\nprint("😀")\n```\n'
        self.app.append(user,role='user'); self.app.append(answer,role='assistant')
        self.assertTrue(self.app.output.tag_ranges('user'))
        self.assertTrue(self.app.output.tag_ranges('assistant'))
        self.assertTrue(self.app.output.tag_ranges('bold'))
        self.assertTrue(self.app.output.tag_ranges('code'))
        self.assertTrue(self.app.output.tag_ranges('inline'))
        self.assertTrue(self.app.output.tag_ranges('markup'))
        self.assertEqual(self.app.output.get('1.0','end-1c'),user+answer)
        self.app.copy_conversation()
        self.assertEqual(self.root.clipboard_get(),user+answer)
        self.assertFalse(self.app.welcome.winfo_manager())

    def test_responsive_chat_layout_and_attachment_chip_removal(self):
        self.app.append('Preview reply\n',role='assistant')
        for index in range(4): self.app.add_attachment(f'very_long_source_name_{index}.php','<?php echo 1;')
        for width,height,sidebar in ((1120,820,True),(760,760,False),(660,580,False)):
            self.app.window.geometry(f'{width}x{height}'); self.root.update(); self.root.update_idletasks()
            self.assertEqual(bool(self.app.sidebar.winfo_manager()),sidebar)
            self.assertEqual(bool(self.app.compact_mode.winfo_manager()),not sidebar)
            self.assertTrue(self.app.more_button.winfo_ismapped())
            self.assertGreater(self.app.output.winfo_height(),20)
            self.assertLessEqual(self.app.composer.winfo_rooty()+self.app.composer.winfo_height(),
                                 self.app.window.winfo_rooty()+self.app.window.winfo_height())
            for button in self.app.chip_buttons:
                self.assertLessEqual(button.winfo_x()+button.winfo_width(),self.app.chips.winfo_width())
            self.assertEqual(str(self.app.prompt.cget('font')),str(self.app.fonts['body']))
        self.app.command.set('Team'); self.app.fields()
        self.app.window.geometry('660x580'); self.root.update(); self.root.update_idletasks()
        self.assertGreater(self.app.output.winfo_height(),20)
        self.assertLessEqual(self.app.composer.winfo_rooty()+self.app.composer.winfo_height(),
                             self.app.window.winfo_rooty()+self.app.window.winfo_height())
        self.app.more_menu.invoke('Copy conversation')
        self.assertEqual(self.root.clipboard_get(),self.app.output.get('1.0','end-1c'))
        self.app.chip_buttons[0].invoke(); self.root.update()
        self.assertEqual(len(self.app.attachments),3)
        self.assertNotIn('source_name_0.php',self.app.attachments[0])

    def test_composer_enter_shift_enter_and_failed_send(self):
        self.app.prompt.insert('1.0','draft'); self.app.prompt.mark_set('insert','end-1c')
        self.app.prompt.focus_force(); self.root.update()
        with patch.object(self.app,'submit') as submit:
            self.app.prompt.event_generate('<KeyPress>',keysym='Return',state=1)
            self.root.update()
            self.assertEqual(self.app.prompt.get('1.0','end-1c'),'draft\n')
            submit.assert_not_called()
            self.app.prompt.event_generate('<KeyPress>',keysym='Return',state=0)
            self.root.update(); submit.assert_called_once()
        with patch.object(self.app,'start',return_value=False): self.app.submit()
        self.assertEqual(self.app.prompt.get('1.0','end-1c'),'draft\n')
        with patch.object(self.app,'start',return_value=True): self.app.submit()
        self.assertEqual(self.app.prompt.get('1.0','end-1c'),'')
        self.app.set_running(True)
        self.assertEqual(str(self.app.stop_button.cget('state')),'normal')
        self.assertEqual(str(self.app.lead.cget('state')),'disabled')
        self.app.set_running(False)
        self.assertEqual(str(self.app.stop_button.cget('state')),'disabled')
        self.assertEqual(str(self.app.lead.cget('state')),'readonly')

    def test_ai_theme_matches_editor_chrome(self):
        style = ttk.Style(self.root)
        style.configure('TFrame', background='#252526')
        style.configure('TEntry', fieldbackground='#1e1e1e')
        style.configure('TButton', background='#2d2d2d')
        style.configure('TLabel', foreground='#cccccc')
        style.configure('Muted.TLabel', foreground='#969696')
        style.configure('Accent.TButton', background='#007acc')
        editor_font = font.nametofont('TkDefaultFont', root=self.root).copy()
        self.api._editor.ui_font = editor_font
        before = editor_font.actual()
        self.app.close()
        with patch.object(self.app, 'load_models'):
            self.app.open('Chat')
        self.root.update()
        self.assertEqual(self.app.colors['side'], '#252526')
        self.assertEqual(self.app.colors['accent'], '#007acc')
        self.assertEqual(self.app.output.cget('background'), '#1e1e1e')
        self.assertEqual(self.app.prompt.cget('background'), '#2d2d2d')
        self.assertEqual(style.lookup('Ai.Send.TButton', 'background'), '#007acc')
        self.assertEqual(style.lookup('Ai.Send.TButton', 'foreground'), '#ffffff')
        self.assertEqual(self.app.fonts['body'].cget('family'), editor_font.cget('family'))
        self.assertEqual(editor_font.actual(), before)

    def test_current_long_reply_is_not_silently_cut(self):
        self.app.append('Assistant:\n', role='assistant')
        source = ''.join(f'line {index}: ' + 'x' * 100 + '\n' for index in range(2200))
        self.app.append(source, role='assistant')
        self.assertEqual(self.app.output.get('1.0', 'end-1c'), 'Assistant:\n' + source)
        self.app.append('\n```python\n', role='assistant')
        self.app.append('print("kept")\n', role='assistant')
        self.assertTrue(self.app.output.tag_ranges('code'))
        self.assertTrue(self.app.code_block)
        self.app.append('```\n', role='assistant')
        self.assertFalse(self.app.code_block)

    def test_reply_cap_is_opt_in_and_sent_to_backend(self):
        self.app.reply_cap.set('4096')
        self.app.prompt.insert('1.0', 'Give a complete example')
        with patch.object(self.app, 'start', return_value=True) as start:
            self.app.submit()
        self.assertEqual(start.call_args.args[0]['limits']['reply_tokens'], 4096)
        self.assertEqual(ai.FAST_LIMITS['Code']['reply_tokens'], 1536)
        self.assertIn('4,096', self.app.limits_label.cget('text'))

    def test_ai_theme_does_not_change_shared_fonts_or_styles(self):
        default=font.nametofont('TkDefaultFont',root=self.root)
        fixed=font.nametofont('TkFixedFont',root=self.root)
        before_default,before_fixed=default.actual(),fixed.actual()
        style=ttk.Style(self.root); before_button=style.lookup('TButton','background')
        self.app.fonts['body'].configure(size=15)
        self.assertEqual(default.actual(),before_default)
        self.assertEqual(fixed.actual(),before_fixed)
        self.assertEqual(style.lookup('TButton','background'),before_button)


    def test_structured_rate_limit_status_and_provider_pause(self):
        event={'type':'error','message':'Provider is rate-limiting requests.','status':429,'provider':'Provider','retry_after':30}
        class Process:
            def __init__(self,*args,**kwargs):
                self.stdin=io.StringIO(); self.stdout=io.StringIO('EVENT:'+json.dumps(event)+'\n'); self.returncode=1
            def poll(self): return self.returncode
            def wait(self,**kwargs): return self.returncode
            def kill(self): self.returncode=-9
        with patch.object(ai.subprocess,'Popen',Process):
            self.app.start({'script':str(ai.SCRIPTS/'chat_ai.py'),'command':'ask'})
            deadline=time.monotonic()+3
            while self.app.process is not None and time.monotonic()<deadline:
                self.root.update(); time.sleep(.01)
        self.assertEqual(self.app.status.cget('text'),'Rate limited')
        self.assertIn('rate-limiting',self.app.output.get('1.0','end'))
        self.app.prompt.insert('1.0','Try again')
        with patch.object(self.app,'start') as start, patch.object(ai.messagebox,'showinfo') as info:
            self.app.submit()
        start.assert_not_called(); info.assert_called_once()
        self.assertEqual(self.app.prompt.get('1.0','end-1c'),'Try again')
        self.app.cooldowns['Provider']=time.monotonic()-1
        with patch.object(self.app,'start',return_value=True) as start:
            self.app.submit()
        start.assert_called_once()

    def test_background_output_history_and_cancel(self):
        class Process:
            def __init__(self, *args, **kwargs):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO('reply\nHISTORY:' + json.dumps([
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "reply"}]) + '\n')
                self.returncode = 0
            def poll(self): return self.returncode
            def wait(self, **kwargs): return self.returncode
            def kill(self): self.returncode = -9
        key = ("Code", "Provider", "lead")
        with patch.object(ai.subprocess, "Popen", Process):
            self.app.start({"script": str(ai.SCRIPTS / "code_ai.py"), "command": "ask"}, key)
            deadline = time.monotonic() + 3
            while self.app.process is not None and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
        self.assertIsNone(self.app.process)
        self.assertEqual(len(self.app.histories[key]), 2)
        self.assertIn("reply", self.app.output.get("1.0", "end"))
        process = Mock()
        self.app.process = process
        self.app.cancel()
        process.kill.assert_called_once()
        self.assertIsNone(self.app.process)


if __name__ == "__main__":
    unittest.main(verbosity=2)
