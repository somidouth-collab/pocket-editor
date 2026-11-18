"""Explicit opt-in live verification of the AI plugin; never run on import.

Uses configured providers and harmless synthetic prompts; attaches no files.
Run: python3 -B tools/check_ai_online.py --online
"""
import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pocket_editor_plugins.ai_assistant import Assistant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='Allow actual API requests using configured credentials')
    parser.add_argument('--reported-team', action='store_true', help='Only reproduce the reported ERNIE team-lead path')
    args = parser.parse_args()
    if not args.online:
        parser.error('Live provider requests require the explicit --online flag.')
    root = tk.Tk(); root.withdraw()
    errors = []
    root.report_callback_exception = lambda *values: errors.append(str(values[1]))
    api = SimpleNamespace(_editor=SimpleNamespace(root=root), on_cleanup=lambda callback: None,
                          get_path=lambda: None, get_text=lambda: '')
    app = Assistant(api)
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'live': True,
              'project_files_sent': False, 'cases': [], 'gui_errors': errors}
    destination = ROOT/'audit'/('ai-online-reported-team.json' if args.reported_team else 'ai-online-results.json')
    global_deadline = time.monotonic()+600

    def save():
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    def say(**values):
        print(json.dumps(values, ensure_ascii=False), flush=True)

    def wait(label, maximum):
        start = last = time.monotonic()
        while app.process is not None:
            root.update()
            now = time.monotonic()
            if now-start > maximum or now > global_deadline:
                app.cancel()
                app.status.configure(text='Harness time budget exceeded')
                break
            if now-last >= 15:
                say(case=label, waiting_seconds=round(now-start,1), ui_status=app.status.cget('text'))
                last = now
            time.sleep(.015)
        root.update()
        return round(time.monotonic()-start,2)

    def discover(mode, opening=False):
        say(case='Discover '+mode, state='starting')
        started = time.monotonic()
        if opening:
            app.open(mode)
            app.window.title('Pocket AI — Live online verification')
        else:
            app.mode.set(mode); app.fields(); app.refresh.invoke()
        elapsed = wait('Discover '+mode, 60)
        models = [dict(model) for model in app.models if model['provider'] != 'Ollama (local)'] if str(app.status.cget('text'))=='Finished' else []
        case = {'name':'Discover '+mode, 'status':str(app.status.cget('text')),
                'elapsed_seconds':round(time.monotonic()-started,2), 'models':models}
        report['cases'].append(case); save()
        say(case=case['name'], status=case['status'], models=len(models), elapsed_seconds=elapsed)
        return models

    def find_index(identity):
        return next(index for index,model in enumerate(app.models)
                    if (model['provider'],model['name']) == identity)

    def run_case(name, mode, command, lead, prompt, workers=()):
        if time.monotonic() > global_deadline:
            return None
        app.mode.set(mode); app.command.set(command); app.fields()
        app.lead.current(find_index((lead['provider'],lead['name'])))
        app.workers.selection_clear(0,'end')
        for worker in workers:
            app.workers.selection_set(find_index((worker['provider'],worker['name'])))
        app.prompt.delete('1.0','end'); app.prompt.insert('1.0',prompt)
        begin = app.output.index('end-1c')
        key = (mode,lead['provider'],lead['name'])
        say(case=name, provider=lead['provider'], model=lead['name'], state='starting')
        app.send.invoke()
        elapsed = wait(name, 200 if command=='Team' else 140)
        status = str(app.status.cget('text'))
        transcript = app.output.get(begin,'end-1c')
        history = app.histories.get(key, [])
        reply = history[-1]['content'] if command=='Ask' and status=='Finished' and history else ''
        outcome = ('pass' if status=='Finished' and (reply.strip() or command=='Team') else
                   'partial' if status=='Finished with worker errors' else
                   'provider_rate_limit' if status=='Rate limited' else
                   'timeout' if 'time' in status.lower() else 'failed')
        result = {'name':name, 'mode':mode, 'command':command, 'provider':lead['provider'],
                  'model':lead['name'], 'workers':[{'provider':w['provider'],'model':w['name']} for w in workers],
                  'status':outcome, 'ui_status':status, 'elapsed_seconds':elapsed,
                  'prompt':prompt, 'reply':reply, 'transcript':transcript}
        report['cases'].append(result); save()
        say(case=name,status=outcome,ui_status=status,elapsed_seconds=elapsed,reply=reply[:250])
        return result

    try:
        models = discover('Chat', opening=True)
        if not models:
            report['blocker'] = 'No configured online models were discovered.'
            return
        if args.reported_team:
            lead = next((model for model in models if model['provider']=='OpenRouter' and 'ernie' in model['name'].lower()),None)
            worker = next((model for model in models if model['provider']=='Gemini' and 'flash-lite' in model['name'].lower()),None)
            if lead and worker:
                run_case('Reported ERNIE team lead','Chat','Team',lead,
                    'Review only this supplied Python function: def add(a, b): return a - b. '
                    'It should add the two arguments. Report the bug and show the minimal correction. '
                    'Keep planning and reports short. No files or commands are available.',[worker])
            else:
                report['blocker']='The reported ERNIE lead or configured Gemini worker was unavailable.'
            return
        groups = {}
        for model in models:
            groups.setdefault(model['provider'], []).append(model)
        healthy = []
        for provider, choices in groups.items():
            # One configured model per provider. Reproduce the reported ERNIE path
            # when available; otherwise use the provider's existing display order.
            choice = next((model for model in choices if provider=='OpenRouter' and 'ernie' in model['name'].lower()), choices[0])
            result = run_case('Chat smoke — '+provider,'Chat','Ask',choice,
                              'Reply with exactly POCKET_AI_ONLINE_OK. No explanation.')
            if result and result['status']=='pass':
                result['expected_marker_present'] = 'POCKET_AI_ONLINE_OK' in result['reply']
                healthy.append((choice,result['elapsed_seconds']))
                save()
        if not healthy:
            report['blocker'] = 'No provider returned a successful Chat reply; Code and Team were not sent.'
            return
        healthy.sort(key=lambda pair:pair[1])
        chosen = healthy[0][0]
        capability = run_case('Capability honesty','Chat','Ask',chosen,
            'In one sentence: in Pocket Editor, can you directly read or edit project files, or execute shell commands and tests? State your actual available tools.')
        if capability and capability['status']=='pass':
            text = capability['reply'].lower()
            capability['denies_direct_tool_access'] = any(token in text for token in
                ('cannot', "can't", 'no direct', 'no tools', 'do not have', "don't have", 'not able', 'only text', 'text-only'))
            save()
        code_models = discover('Code')
        eligible = [model for model in code_models
                    if any(model['provider']==candidate['provider'] and model['name']==candidate['name'] for candidate,_ in healthy)
                    and app.cooldowns.get(model['provider'],0) <= time.monotonic()]
        if eligible:
            chosen = next((model for model in eligible if model['provider']==healthy[0][0]['provider']),eligible[0])
            code = run_case('Code generation','Code','Ask',chosen,
                'Return only Python code for def add(a, b) that returns a + b. Do not read files or run anything.')
            if code and code['status']=='pass':
                text = code['reply'].strip()
                if text.startswith('```'):
                    text='\n'.join(text.splitlines()[1:-1])
                try:
                    tree=ast.parse(text)
                    code['valid_add_function'] = any(isinstance(node,ast.FunctionDef) and node.name=='add'
                        and [argument.arg for argument in node.args.args]==['a','b']
                        and any(isinstance(item,ast.Return) and isinstance(item.value,ast.BinOp)
                                and isinstance(item.value.op,ast.Add)
                                and isinstance(item.value.left,ast.Name) and item.value.left.id=='a'
                                and isinstance(item.value.right,ast.Name) and item.value.right.id=='b'
                                for item in ast.walk(node)) for node in tree.body)
                except SyntaxError:
                    code['valid_add_function'] = False
                save()
        # A small team: two distinct developers, three ordinary generation calls.
        healthy_ids={(model['provider'],model['name']) for model,_ in healthy}
        candidates = [model for model in app.models if (model['provider'],model['name']) in healthy_ids
                      and app.cooldowns.get(model['provider'],0)<=time.monotonic()]
        lead = next((model for model in candidates if model['provider']==healthy[0][0]['provider']), candidates[0] if candidates else None)
        worker = next((model for model in candidates if lead and model['developer']!=lead['developer']
                       and model['provider']!=lead['provider']),None)
        if lead and worker:
            run_case('Small team review','Chat','Team',lead,
                'Review only this supplied Python function: def add(a, b): return a - b. '
                'It should add the two arguments. Report the bug and show the minimal correction. '
                'Keep planning and reports short. No files or commands are available.',[worker])
        else:
            report['team_skipped'] = 'No two healthy online models from different developers/providers were available.'
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report['summary'] = {status:sum(case.get('status')==status for case in report['cases'])
                             for status in ('pass','partial','provider_rate_limit','timeout','failed')}
        save(); say(report=str(destination),summary=report['summary'])
        app.cleanup(); root.destroy()


if __name__ == '__main__':
    main()
