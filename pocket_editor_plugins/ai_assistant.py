"""Chat/Code AI frontend with packaged backends and legacy sibling fallback."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import time
import threading
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

BUNDLED_SCRIPTS = Path(__file__).resolve().parents[1] / "tools/ai"
SCRIPTS = BUNDLED_SCRIPTS if BUNDLED_SCRIPTS.is_dir() else Path(__file__).resolve().parents[2] / "scripts"
FAST_LIMITS = {
    "Chat": {"input_bytes": 4096, "team_input_bytes": 4096, "history_bytes": 4096,
             "history_turns": 2, "reply_tokens": 512, "empty_retry_tokens": 2048, "attachments": 2, "workers": 2,
             "timeout_seconds": 60, "team_timeout_seconds": 120},
    "Code": {"input_bytes": 12288, "team_input_bytes": 6144, "history_bytes": 8192,
             "history_turns": 3, "reply_tokens": 1536, "empty_retry_tokens": 3072, "attachments": 4, "workers": 2,
             "timeout_seconds": 120, "team_timeout_seconds": 180},
}


def input_size(prompt, attachments):
    return len("\n\n".join([prompt.strip(), *attachments]).encode("utf-8"))


def recent_history(history, limits):
    """Keep a contiguous suffix of complete turns; never cut a code block in half."""
    pairs = []
    for index in range(0, len(history)-1, 2):
        user, answer = history[index:index+2]
        if (isinstance(user, dict) and isinstance(answer, dict)
                and user.get("role") == "user" and answer.get("role") == "assistant"
                and isinstance(user.get("content"), str) and isinstance(answer.get("content"), str)):
            pairs.append([dict(user), dict(answer)])
    retained, size = [], 0
    for pair in reversed(pairs[-limits["history_turns"]:]):
        count = sum(len(message["content"].encode("utf-8")) for message in pair)
        if size + count > limits["history_bytes"]:
            break
        retained[0:0] = pair
        size += count
    return retained


BRIDGE = r'''
import contextlib, io, json, runpy, sys, time
from email.utils import parsedate_to_datetime

protocol_out = sys.stdout
keys = {}


def emit(kind, **payload):
    protocol_out.write("EVENT:" + json.dumps({"type": kind, **payload}) + "\n")
    protocol_out.flush()


def retry_delay(error):
    current = error
    for _ in range(4):
        headers = getattr(current, "headers", None)
        value = headers.get("Retry-After") if headers is not None else None
        if value:
            try:
                delay = float(value) if str(value).strip().isdigit() else parsedate_to_datetime(value).timestamp() - time.time()
                return max(1, min(86400, int(delay)))
            except (ValueError, TypeError, OverflowError):
                pass
        current = getattr(current, "__cause__", None)
        if current is None:
            break
    return 30


def error_details(error, model=None):
    status = getattr(error, "status", None)
    provider = model.provider.name if model is not None else ""
    label = provider + " / " + model.name if model is not None else "AI service"
    if status == 429:
        message = label + " is rate-limiting requests (HTTP 429). Wait before retrying or choose another provider. Provider quotas may last longer than the local pause."
    elif status in (401, 403):
        message = label + " rejected authentication or access. Check the provider key and model access using /keys in the AI scripts."
    elif status == 402:
        message = label + " reports insufficient quota or credits. Check the provider account or choose another provider."
    elif status in (500, 502, 503, 504):
        message = label + " is temporarily unavailable. Try later or select another provider."
    elif "empty reply" in str(error).lower():
        message = label + " returned no visible reply after one retry. It may need more reasoning/output tokens or be temporarily unavailable. Try another model."
    elif isinstance(error, (ValueError, EOFError, OSError)) or type(error).__name__ == "APIError":
        message = label + ": " + " ".join(str(error).split())[:500]
    else:
        message = label + " could not complete the request (" + type(error).__name__ + "). Refresh models and try again."
    for key in keys.values():
        if isinstance(key, str) and key:
            message = message.replace(key, "[redacted]")
    return {"message": message, "status": status, "provider": provider,
            "retry_after": retry_delay(error) if status == 429 else 0}


class TeamOutput:
    def __init__(self): self.pending = ""
    def write(self, value):
        self.pending += value
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            emit("text", text=line + "\n", role="assistant")
        return len(value)
    def flush(self):
        if self.pending:
            emit("text", text=self.pending, role="assistant")
            self.pending = ""


def run():
    global keys
    request = json.load(sys.stdin)
    m = runpy.run_path(request["script"])
    with contextlib.redirect_stdout(io.StringIO()) as log:
        models, keys = m["configure"](m["load_credentials"](False))
    if request["command"] == "list":
        print("MODELS:" + json.dumps([{"label": x.label, "provider": x.provider.name,
              "name": x.name, "developer": m["model_developer"](x)} for x in models]), flush=True)
        if log.getvalue(): emit("text", text=log.getvalue(), role="notice")
        return 0
    def find(identity):
        for model in models:
            if [model.provider.name, model.name] == identity:
                return model
        raise ValueError("Selected model is unavailable. Refresh models and select again.")
    attachments, prompt, limits = request["attachments"], request["prompt"], request["limits"]
    original_ask = m["ask"]
    lead = find(request["lead"])
    lead_failure = None
    worker_failures = {}
    def fast_ask(model, key, history, prompt, max_output_tokens=None):
        nonlocal lead_failure
        maximum = limits["reply_tokens"]
        if max_output_tokens is not None: maximum = min(maximum, max_output_tokens)
        try:
            try:
                answer = original_ask(model, key, history, prompt, max_output_tokens=maximum)
            except Exception as error:
                if getattr(error, "status", None) is not None or "empty reply" not in str(error).lower():
                    raise
                retry_maximum = limits.get("empty_retry_tokens", maximum * 2)
                emit("notice", text="No visible reply from " + model.provider.name + " / " + model.name
                     + ". Retrying once with up to " + str(retry_maximum) + " output/reasoning tokens.\n")
                answer = original_ask(model, key, history, prompt, max_output_tokens=retry_maximum)
            identity = (model.provider.name, model.name)
            if identity == (lead.provider.name, lead.name): lead_failure = None
            worker_failures.pop(identity, None)
            return answer
        except Exception as error:
            details = error_details(error, model)
            if request["command"] == "team":
                if model.provider.name == lead.provider.name and model.name == lead.name:
                    lead_failure = details
                else:
                    worker_failures[(model.provider.name, model.name)] = details
                    emit("warning", **details)
                error_type = m.get("APIError", RuntimeError)
                if isinstance(error, error_type):
                    raise error_type(details["message"], status=details["status"]) from None
            emit("error", **details)
            raise RequestFailed() from None
    original_ask.__globals__["ask"] = fast_ask
    original_ask.__globals__["SYSTEM_PROMPT"] = m.get("SYSTEM_PROMPT", "") + (
        " Actual Pocket Editor capabilities: you are a text-only assistant. You receive only the user's prompt,"
        " explicitly attached source text and retained conversation. You have NO file/project browsing, file-writing,"
        " command-execution or test-running tools. You may explain supplied code and propose patches, which the user"
        " must apply and verify. Never claim you can open/read/edit project files directly, inspect the environment,"
        " run commands/tests, or commit changes. Any earlier claim of such access was incorrect."
        " Keep replies concise; prefer focused patches over full-file rewrites."
    )
    if request["command"] == "team":
        workers = [find(identity) for identity in request["workers"]]
        developers = {m["model_developer"](lead)}
        for worker in workers:
            developer = m["model_developer"](worker)
            if developer in developers: raise ValueError("Team needs one model per developer, including the lead.")
            developers.add(developer)
        checks = m["python_attachment_checks"](attachments) if m["CODING"] else ()
        task = "\n\n".join([prompt, *attachments])
        verification = "\n\nLocal Python syntax checks (no code executed):\n" + "\n".join(checks) if checks else ""
        if len((task + verification).encode("utf-8")) > m.get("TEAM_PROMPT_BYTES", 16000) // 2:
            raise ValueError("Task plus syntax-check metadata exceeds team source context. Attach smaller relevant excerpts.")
        output = TeamOutput()
        try:
            with contextlib.redirect_stdout(output): m["run_team"](lead, workers, keys, task, checks)
        finally: output.flush()
        if lead_failure:
            emit("error", **lead_failure)
            return 1
        if worker_failures: emit("partial", text="Finished with worker errors; only available reports were used.\n")
    else:
        answer = fast_ask(lead, keys.get(lead.provider, ""), request["history"], "\n\n".join([prompt, *attachments]))
        emit("text", text=answer + "\n", role="assistant")
        print("HISTORY:" + json.dumps([{"role": "user", "content": "\n\n".join([prompt, *attachments])},
                                      {"role": "assistant", "content": answer}]), flush=True)
    return 0


class RequestFailed(Exception): pass

try:
    exit_code = run()
except RequestFailed:
    exit_code = 1
except (Exception, SystemExit) as error:
    emit("error", **error_details(error))
    exit_code = 1
raise SystemExit(exit_code)
'''


def build_request(mode, command, models, lead_index, worker_indices, prompt, attachments, history=(), reply_tokens=None):
    if mode not in ("Chat", "Code") or command not in ("Ask", "Team"):
        raise ValueError("Select Chat or Code and Ask or Team.")
    if not prompt.strip():
        raise ValueError("Enter a task or question first.")
    if not 0 <= lead_index < len(models):
        raise ValueError("Refresh models and select a model or team lead.")
    limits = dict(FAST_LIMITS[mode])
    if reply_tokens is not None:
        if type(reply_tokens) is not int or not 64 <= reply_tokens <= 8192:
            raise ValueError("Reply cap must be between 64 and 8,192 tokens.")
        limits['reply_tokens'] = reply_tokens
        limits['empty_retry_tokens'] = max(reply_tokens, limits['empty_retry_tokens'])
    worker_indices = worker_indices if command == "Team" else ()
    if len(attachments) > limits["attachments"]:
        raise ValueError(f"{mode} fast mode allows {limits['attachments']} attachments. Choose fewer relevant files.")
    maximum = limits["team_input_bytes"] if command == "Team" else limits["input_bytes"]
    size = input_size(prompt, attachments)
    if size > maximum:
        raise ValueError(f"Task and attachments use {size:,} UTF-8 bytes; {mode} / {command} allows {maximum:,}. Shorten the input or attach smaller excerpts.")
    if len(worker_indices) > limits["workers"]:
        raise ValueError(f"Fast team mode allows at most {limits['workers']} workers plus the lead.")
    if any(type(index) is not int or not 0 <= index < len(models) for index in worker_indices):
        raise ValueError("Select valid worker models.")
    retained = recent_history(history, limits) if command == "Ask" else []
    identity = lambda item: [item["provider"], item["name"]]
    workers = [identity(models[i]) for i in worker_indices]
    if command == "Team" and (not workers or identity(models[lead_index]) in workers):
        raise ValueError("Select at least one worker and exclude the lead.")
    if command == "Team":
        developers = [models[i].get("developer", models[i]["provider"])
                      for i in [lead_index, *worker_indices]]
        if len(set(developers)) != len(developers):
            raise ValueError("Choose one model per developer, including the lead.")
    return {"script": str(SCRIPTS / ("code_ai.py" if mode == "Code" else "chat_ai.py")),
            "command": command.lower(), "lead": identity(models[lead_index]),
            "workers": workers, "prompt": prompt.strip(), "attachments": attachments,
            "history": retained, "history_omitted": len(history) - len(retained) if command == "Ask" else 0,
            "limits": limits, "timeout_seconds": limits["team_timeout_seconds"] if command == "Team" else limits["timeout_seconds"]}


class Assistant:
    def __init__(self, api):
        self.api = api
        self.root = api._editor.root
        self.window = None
        self.process = None
        self.timer = None
        self.models = []
        self.histories = {}
        self.attachments = []
        self.mode = None
        self.current_role = "notice"
        self.code_block = False
        self.cooldowns = {}
        api.on_cleanup(self.cleanup)

    def theme(self):
        """Style only AI widgets; document fonts and the editor theme stay independent."""
        editor = self.api._editor
        style = ttk.Style(self.window)
        shared = getattr(editor, "ui_font", None)
        def chrome(name, option, fallback):
            return (style.lookup(name, option) if shared is not None else "") or fallback
        self.colors = {"bg":chrome("TEntry", "fieldbackground", "#1e1e1e"),
                       "side":chrome("TFrame", "background", "#252526"),
                       "panel":chrome("TButton", "background", "#2d2d2d"),
                       "border":"#333333", "text":chrome("TLabel", "foreground", "#cccccc"),
                       "muted":chrome("Muted.TLabel", "foreground", "#969696"),
                       "accent":chrome("Accent.TButton", "background", "#007acc"),
                       "user":"#252526", "answer":"#1e1e1e"}
        base = getattr(self.api._editor,"ui_font",None) or font.nametofont("TkDefaultFont",root=self.root)
        self.fonts = {}
        for name,size,weight in (("body",10,"normal"),("small",9,"normal"),
                ("label",10,"bold"),("title",13,"bold"),("brand",11,"bold"),("bold",10,"bold")):
            instance=base.copy(); instance.configure(size=size,weight=weight); self.fonts[name]=instance
        self.fonts['mono']=(getattr(editor, 'editor_font', None) or font.nametofont('TkFixedFont',root=self.root)).copy()
        self.fonts['mono'].configure(size=10)
        c=self.colors; style=ttk.Style(self.window)
        for name,color in (("Ai.TFrame",c['bg']),("Ai.Side.TFrame",c['side']),
                           ("Ai.Panel.TFrame",c['panel'])):
            style.configure(name,background=color)
        style.configure('Ai.TLabel',background=c['bg'],foreground=c['text'],font=self.fonts['body'])
        style.configure('Ai.Title.TLabel',background=c['bg'],foreground=c['text'],font=self.fonts['title'])
        style.configure('Ai.Muted.TLabel',background=c['bg'],foreground=c['muted'],font=self.fonts['small'])
        style.configure('Ai.Side.TLabel',background=c['side'],foreground=c['muted'],font=self.fonts['small'])
        style.configure('Ai.Brand.TLabel',background=c['side'],foreground=c['text'],font=self.fonts['brand'])
        style.configure('Ai.Panel.TLabel',background=c['panel'],foreground=c['muted'],font=self.fonts['small'])
        style.configure('Ai.TButton',padding=(10,4),width=0,background=c['panel'],foreground=c['text'],
                        font=self.fonts['small'],borderwidth=0,relief='flat')
        style.map('Ai.TButton',background=[('active','#3c3c3c'),('pressed','#454545')],foreground=[('disabled','#777777')])
        style.configure('Ai.Send.TButton',padding=(14,5),background=c['accent'],foreground='#ffffff',font=self.fonts['label'])
        style.map('Ai.Send.TButton',background=[('disabled','#3c3c3c'),('pressed','#005a9e'),('active','#168bd2')],
                  foreground=[('disabled','#969696'),('!disabled','#ffffff')])
        style.configure('Ai.Quiet.TButton',background=c['side'],foreground=c['muted'],padding=(9,4))
        style.map('Ai.Quiet.TButton',background=[('active','#353535')],foreground=[('active',c['text'])])
        style.map('Ai.Chip.TButton',background=[('pressed','#454545'),('active','#3c3c3c')])
        style.configure('Ai.Chip.TButton',padding=(8,3),background='#333333',foreground='#cccccc',font=self.fonts['small'])
        style.configure('Ai.TMenubutton',background=c['panel'],foreground=c['text'],padding=(9,4),
                        borderwidth=0,font=self.fonts['small'],arrowcolor=c['muted'])
        style.configure('Ai.TCombobox',fieldbackground=c['panel'],background=c['panel'],foreground=c['text'],
                        arrowcolor=c['muted'],padding=6,font=self.fonts['small'])
        style.map('Ai.TCombobox',fieldbackground=[('readonly',c['panel'])],foreground=[('readonly',c['text']),('disabled','#777777')],
                  selectbackground=[('readonly',c['panel'])],selectforeground=[('readonly',c['text'])])
        style.configure('Ai.Vertical.TScrollbar',background='#424242',troughcolor=c['bg'],borderwidth=0,arrowsize=10)
        style.map('Ai.Vertical.TScrollbar',background=[('active','#565656')])

    def open(self, mode="Chat"):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            if self.process is None:
                self.mode.set(mode); self.fields()
            return
        self.window=tk.Toplevel(self.root)
        self.window.title('Pocket AI — Chat & Code')
        self.window.geometry('1120x820'); self.window.minsize(660,580)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.theme(); c=self.colors
        self.window.configure(background=c['bg'])
        self.window.columnconfigure(1,weight=1); self.window.rowconfigure(0,weight=1)
        self.mode=tk.StringVar(self.window,mode); self.command=tk.StringVar(self.window,'Ask')
        self.sidebar=ttk.Frame(self.window,style='Ai.Side.TFrame',padding=(12,14),width=190)
        self.sidebar.grid(row=0,column=0,sticky='nsew'); self.sidebar.pack_propagate(False)
        brand=ttk.Frame(self.sidebar,style='Ai.Side.TFrame'); brand.pack(fill='x',pady=(0,12))
        mark=tk.Canvas(brand,width=3,height=20,background=c['side'],highlightthickness=0)
        mark.pack(side='left',padx=(0,10)); mark.create_rectangle(0,0,3,20,fill=c['accent'],outline='')
        ttk.Label(brand,text='Pocket AI',style='Ai.Brand.TLabel').pack(side='left')
        ttk.Label(self.sidebar,text='YOUR WORKSPACE',style='Ai.Side.TLabel').pack(anchor='w',pady=(0,10))
        self.mode_buttons=[]
        for name,caption in (('Chat','Chat & questions'),('Code','Code assistant')):
            button=ttk.Button(self.sidebar,text=caption,style='Ai.TButton',command=lambda name=name:self.set_mode(name))
            button.pack(fill='x',pady=3); self.mode_buttons.append((name,button))
        ttk.Label(self.sidebar,text='CONVERSATION',style='Ai.Side.TLabel').pack(anchor='w',pady=(20,8))
        self.reset_button=ttk.Button(self.sidebar,text='Reset context',command=self.reset,style='Ai.Quiet.TButton')
        self.reset_button.pack(fill='x',pady=3)
        ttk.Button(self.sidebar,text='Copy conversation',command=self.copy_conversation,style='Ai.Quiet.TButton').pack(fill='x',pady=3)
        footer=ttk.Frame(self.sidebar,style='Ai.Side.TFrame'); footer.pack(side='bottom',fill='x')
        ttk.Label(footer,text='Only attached files are sent.\nReplies never edit your files.',style='Ai.Side.TLabel',wraplength=170).pack(anchor='w',pady=(0,14))
        self.refresh=ttk.Button(footer,text='Refresh models',command=self.load_models,style='Ai.Quiet.TButton')
        self.refresh.pack(fill='x',pady=3)
        ttk.Button(footer,text='Help & limits',command=self.help,style='Ai.Quiet.TButton').pack(fill='x',pady=3)
        self.main=ttk.Frame(self.window,style='Ai.TFrame',padding=(16,14))
        self.main.grid(row=0,column=1,sticky='nsew'); self.main.columnconfigure(0,weight=1); self.main.rowconfigure(2,weight=1)
        self.main.bind('<Configure>',self.resize_content)
        header=ttk.Frame(self.main,style='Ai.TFrame'); header.grid(row=0,column=0,sticky='ew',pady=(0,12))
        header.columnconfigure(0,weight=1)
        self.heading=ttk.Label(header,text='Code workspace',style='Ai.Title.TLabel'); self.heading.grid(row=0,column=0,sticky='w')
        self.status=ttk.Label(header,text='Ready',style='Ai.Muted.TLabel'); self.status.grid(row=0,column=1,sticky='e',padx=(12,0))
        self.more_menu=tk.Menu(header,tearoff=False,background=c['panel'],foreground=c['text'],
                               activebackground='#3c3c3c',activeforeground='#ffffff',font=self.fonts['small'])
        for label,action in (('Copy conversation',self.copy_conversation),('Reset context',self.reset),
                             ('Refresh models',self.load_models),('Help & limits',self.help)):
            self.more_menu.add_command(label=label,command=action)
        self.more_button=ttk.Menubutton(header,text='More',menu=self.more_menu,style='Ai.TMenubutton')
        self.more_button.grid(row=0,column=2,sticky='e',padx=(12,0))
        ttk.Label(header,text='A thoughtful assistant, right beside your editor.',style='Ai.Muted.TLabel').grid(row=1,column=0,sticky='w',pady=(5,0))
        self.options=ttk.Frame(self.main,style='Ai.TFrame'); self.options.grid(row=1,column=0,sticky='ew',pady=(0,12))
        model_row=ttk.Frame(self.options,style='Ai.TFrame'); model_row.pack(fill='x')
        model_row.columnconfigure(1,weight=1)
        self.lead_label=ttk.Label(model_row,text='Model',style='Ai.Muted.TLabel'); self.lead_label.grid(row=0,column=0,sticky='w',padx=(0,10))
        self.lead=ttk.Combobox(model_row,state='readonly',style='Ai.TCombobox',width=20); self.lead.grid(row=0,column=1,sticky='ew')
        self.command_picker=ttk.Combobox(model_row,textvariable=self.command,values=('Ask','Team'),state='readonly',width=7,style='Ai.TCombobox')
        self.command_picker.grid(row=0,column=2,padx=(8,0)); self.command_picker.bind('<<ComboboxSelected>>',lambda event:self.fields())
        self.compact_mode=ttk.Combobox(model_row,textvariable=self.mode,values=('Chat','Code'),state='readonly',width=7,style='Ai.TCombobox')
        self.compact_mode.grid(row=0,column=3,padx=(8,0)); self.compact_mode.bind('<<ComboboxSelected>>',lambda event:self.fields())
        self.compact_mode.grid_remove()
        budget_row=ttk.Frame(self.options,style='Ai.TFrame'); budget_row.pack(fill='x',pady=(8,0))
        ttk.Label(budget_row,text='Reply tokens',style='Ai.Muted.TLabel').pack(side='left',padx=(0,8))
        self.reply_cap=tk.StringVar(self.window,'Auto')
        self.reply_picker=ttk.Combobox(budget_row,textvariable=self.reply_cap,values=('Auto','512','1024','2048','4096','8192'),state='readonly',width=8,style='Ai.TCombobox')
        self.reply_picker.pack(side='left')
        ttk.Label(budget_row,text='Larger replies may use more credits',style='Ai.Muted.TLabel').pack(side='left',padx=8)
        self.reply_cap.trace_add('write',lambda *args:self.attachment_status())
        self.team=ttk.Frame(self.options,style='Ai.TFrame')
        ttk.Label(self.team,text='Up to 2 workers · Ctrl-click to select · One model per developer',style='Ai.Muted.TLabel').pack(anchor='w',pady=(10,6))
        self.workers=tk.Listbox(self.team,selectmode='extended',exportselection=False,height=3,font=self.fonts['small'],
            background=c['panel'],foreground=c['text'],selectbackground='#264f78',selectforeground='#ffffff',
            highlightthickness=1,highlightbackground=c['border'],borderwidth=0,activestyle='none')
        self.workers.pack(fill='x')
        conversation=ttk.Frame(self.main,style='Ai.TFrame'); conversation.grid(row=2,column=0,sticky='nsew')
        self.output=ScrolledText(conversation,height=1,width=1,wrap='word',state='disabled',font=self.fonts['body'],
            background=c['bg'],foreground=c['text'],insertbackground=c['accent'],selectbackground='#264f78',
            selectforeground='#ffffff',inactiveselectbackground='#264f78',
            borderwidth=0,highlightthickness=0,padx=12,pady=10,spacing1=2,spacing3=6)
        self.output.pack(fill='both',expand=True); self.output.frame.configure(background=c['bg'])
        self.output.vbar.configure(background='#424242',troughcolor=c['bg'],borderwidth=0,width=10)
        self.output.tag_configure('user',background=c['user'],foreground='#dddddd',lmargin1=130,lmargin2=130,rmargin=12,spacing1=3,spacing3=4)
        self.output.tag_configure('assistant',background=c['answer'],lmargin1=12,lmargin2=12,rmargin=42,spacing1=2,spacing3=4)
        self.output.tag_configure('notice',foreground=c['muted'],font=self.fonts['small'],lmargin1=12,lmargin2=12,rmargin=12)
        self.output.tag_configure('role',foreground=c['accent'],font=self.fonts['label'])
        self.output.tag_configure('bold',font=self.fonts['bold'])
        self.output.tag_configure('heading',font=self.fonts['brand'],foreground='#eeeeee')
        self.output.tag_configure('code',font=self.fonts['mono'],background='#181818',foreground='#ce9178',lmargin1=26,lmargin2=26,rmargin=25,spacing1=2,spacing3=2)
        self.output.tag_configure('inline',font=self.fonts['mono'],foreground='#9cdcfe',background='#2d2d2d')
        self.output.tag_configure('markup',elide=True)
        self.output.tag_raise('sel')
        self.welcome=ttk.Frame(conversation,style='Ai.TFrame',padding=20)
        self.welcome.place(relx=.5,rely=.44,anchor='center')
        ttk.Label(self.welcome,text='What can we work on?',style='Ai.Title.TLabel').pack(pady=(0,9))
        ttk.Label(self.welcome,text='Ask a question, attach context, or start with an idea.',style='Ai.Muted.TLabel').pack(pady=(0,22))
        examples=ttk.Frame(self.welcome,style='Ai.TFrame'); examples.pack()
        for title,prompt in (('Explain code','Explain the attached code clearly.'),('Find a bug','Review the attached code for bugs and suggest a focused fix.'),('Plan a change','Help me plan a small, maintainable change.')):
            ttk.Button(examples,text=title,style='Ai.TButton',command=lambda prompt=prompt:self.use_example(prompt)).pack(side='left',padx=4)
        self.composer=ttk.Frame(self.main,style='Ai.Panel.TFrame',padding=10)
        self.composer.grid(row=3,column=0,sticky='ew',pady=(10,0)); self.composer.columnconfigure(0,weight=1)
        self.chip_buttons=[]
        self.chips=ttk.Frame(self.composer,style='Ai.Panel.TFrame')
        self.chips.bind('<Configure>',lambda event:self.layout_chips())
        self.chips.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,5))
        self.task_label=ttk.Label(self.composer,text='Message',style='Ai.Panel.TLabel'); self.task_label.grid(row=1,column=0,sticky='w',pady=(0,5))
        self.prompt=ScrolledText(self.composer,height=2,width=1,wrap='word',font=self.fonts['body'],background=c['panel'],
            foreground=c['text'],insertbackground=c['accent'],selectbackground='#264f78',borderwidth=0,highlightthickness=0,padx=3,pady=5)
        self.prompt.grid(row=2,column=0,columnspan=2,sticky='ew')
        self.prompt.frame.configure(background=c['panel']); self.prompt.vbar.configure(background=c['panel'],troughcolor=c['panel'],borderwidth=0,width=9)
        self.placeholder=tk.Label(self.prompt,text='Ask anything about your work…',font=self.fonts['body'],background=c['panel'],foreground='#969696',cursor='xterm')
        self.placeholder.place(x=3,y=5); self.placeholder.bind('<Button-1>',lambda event:self.prompt.focus_set())
        self.prompt.bind('<FocusIn>',lambda event:self.placeholder.place_forget())
        self.prompt.bind('<FocusOut>',lambda event:self.update_placeholder())
        self.prompt.bind('<Return>',self.send_enter); self.prompt.bind('<KP_Enter>',self.send_enter)
        controls=ttk.Frame(self.composer,style='Ai.Panel.TFrame'); controls.grid(row=3,column=0,sticky='w',pady=(8,0))
        ttk.Button(controls,text='+ Files',command=self.attach_files,style='Ai.TButton').pack(side='left')
        ttk.Button(controls,text='Current document',command=self.attach_document,style='Ai.TButton').pack(side='left',padx=5)
        ttk.Button(controls,text='Clear files',command=self.clear_attachments,style='Ai.TButton').pack(side='left')
        actions=ttk.Frame(self.composer,style='Ai.Panel.TFrame'); actions.grid(row=3,column=1,sticky='e',pady=(8,0))
        self.stop_button=ttk.Button(actions,text='Stop',command=self.cancel,style='Ai.TButton',state='disabled'); self.stop_button.pack(side='left',padx=(6,6))
        self.send=ttk.Button(actions,text='Send',command=self.submit,style='Ai.Send.TButton'); self.send.pack(side='left')
        self.attached=ttk.Label(self.main,text='No files attached',style='Ai.Muted.TLabel'); self.attached.grid(row=4,column=0,sticky='w',pady=(8,0))
        self.limits_label=ttk.Label(self.main,text='',wraplength=820,style='Ai.Muted.TLabel'); self.limits_label.grid(row=5,column=0,sticky='ew',pady=(5,0))
        ttk.Label(self.main,text='Enter to send · Shift+Enter for a new line · AI can make mistakes.',style='Ai.Muted.TLabel').grid(row=6,column=0,sticky='w',pady=(7,0))
        self.attachments=[]; self.current_role='notice'; self.code_block=False
        self.prompt.bind('<<Modified>>',self.prompt_changed)
        self.window.bind('<Configure>',self.resize)
        self.fields(); self.load_models()

    def set_mode(self,mode):
        if self.process is None:
            self.mode.set(mode); self.fields()

    def fields(self):
        self.attachment_status()
        team=self.command.get()=='Team'
        self.window.minsize(660,660 if team else 580)
        self.heading.configure(text='Code workspace' if self.mode.get()=='Code' else 'Chat workspace')
        self.lead_label.configure(text='Team lead' if team else 'Model')
        self.task_label.configure(text='Team task' if team else 'Message')
        for name,button in self.mode_buttons:
            button.configure(style='Ai.Send.TButton' if name==self.mode.get() else 'Ai.TButton')
        if team: self.team.pack(fill='x',pady=(0,5))
        else: self.team.pack_forget()

    def resize(self,event):
        if event.widget is not self.window: return
        narrow=self.window.winfo_width()<920
        if narrow:
            self.sidebar.grid_remove(); self.compact_mode.grid()
        else:
            self.sidebar.grid(); self.compact_mode.grid_remove()
        self.resize_content()

    def resize_content(self,event=None):
        width=max(300,self.main.winfo_width()-32)
        self.limits_label.configure(wraplength=width)
        self.output.tag_configure('user',lmargin1=max(30,int(width*.17)),lmargin2=max(30,int(width*.17)))

    def use_example(self,value):
        if self.process is None:
            self.prompt.delete('1.0','end'); self.prompt.insert('1.0',value); self.prompt.focus_set()

    def update_placeholder(self):
        if self.prompt.get('1.0','end-1c') or self.window.focus_get() is self.prompt:
            self.placeholder.place_forget()
        else: self.placeholder.place(x=3,y=5)

    def send_enter(self,event):
        if event.state & 1: return None
        self.submit(); return 'break'

    def set_running(self,running):
        self.send.configure(state='disabled' if running else 'normal')
        self.refresh.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')
        self.reset_button.configure(state='disabled' if running else 'normal')
        for _,button in self.mode_buttons: button.configure(state='disabled' if running else 'normal')
        for widget in (self.lead,self.command_picker,self.compact_mode,self.reply_picker): widget.configure(state='disabled' if running else 'readonly')

    def append(self,text,role=None):
        new_role = role is not None and role != self.current_role
        if role is not None:
            self.current_role=role
            if new_role: self.code_block=False
        self.welcome.place_forget()
        follow=self.output.yview()[1]>.97
        self.output.configure(state='normal')
        start=self.output.index('end-1c')
        if self.current_role=='assistant' and (new_role or text.startswith(('Assistant:\n','Team output:\n'))):
            self.output.mark_set('latest_reply',start)
            self.output.mark_gravity('latest_reply','left')
        self.output.insert('end',text,(self.current_role,))
        if text.startswith(('You:\n','Assistant:\n','Team output:\n')):
            self.output.tag_add('role',start,f'{start} lineend')
        if self.current_role=='assistant':
            self.format_reply(start,text)
        if len(self.output.get('1.0','end-1c'))>200000:
            cutoff=self.output.index('end-200000c')
            if 'latest_reply' in self.output.mark_names() and self.output.compare(cutoff,'>','latest_reply'):
                cutoff=self.output.index('latest_reply')
            self.output.delete('1.0',cutoff)
        if follow: self.output.see('end')
        self.output.configure(state='disabled')

    def format_reply(self,start,value):
        import re
        offset=0
        for line in value.splitlines(keepends=True):
            begin=f'{start} +{offset}c'; finish=f'{start} +{offset+len(line)}c'
            stripped=line.lstrip()
            if stripped.startswith('```'):
                self.code_block=not self.code_block
                self.output.tag_add('code',begin,finish)
                marker=len(line)-len(stripped)
                self.output.tag_add('markup',f'{begin} +{marker}c',f'{begin} +{marker+3}c')
            elif self.code_block:
                self.output.tag_add('code',begin,finish)
            else:
                if re.match(r'^#{1,3}\s',line): self.output.tag_add('heading',begin,finish)
                for pattern,tag,padding in ((r'\*\*([^\n]+?)\*\*','bold',2),(r'`([^`\n]+)`','inline',1)):
                    for match in re.finditer(pattern,line):
                        self.output.tag_add(tag,f'{begin} +{match.start()}c',f'{begin} +{match.end()}c')
                        self.output.tag_add('markup',f'{begin} +{match.start()}c',f'{begin} +{match.start()+padding}c')
                        self.output.tag_add('markup',f'{begin} +{match.end()-padding}c',f'{begin} +{match.end()}c')
            offset+=len(line)

    def load_models(self):
        if self.process is not None:
            return
        script = SCRIPTS / ("code_ai.py" if self.mode.get() == "Code" else "chat_ai.py")
        self.start({"script": str(script), "command": "list", "timeout_seconds": 45})

    def start(self, request, history_key=None):
        if self.process is not None:
            return False
        if not Path(request["script"]).is_file():
            messagebox.showerror("AI scripts missing", str(request["script"]), parent=self.window)
            return False
        events = queue.Queue(maxsize=128)
        stop = threading.Event()
        try:
            process = subprocess.Popen([sys.executable, "-B", "-u", "-c", BRIDGE],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8")
            process.stdin.write(json.dumps(request))
            process.stdin.close()
        except (OSError, BrokenPipeError) as error:
            messagebox.showerror("AI request", str(error), parent=self.window)
            return False
        self.process = process
        self.stop = stop
        self.set_running(True)
        self.status.configure(text="Loading models…" if request["command"] == "list" else "Running…")
        def reader():
            try:
                for line in process.stdout:
                    while not stop.is_set():
                        try:
                            events.put(line[:100000], timeout=.1)
                            break
                        except queue.Full:
                            continue
                    if stop.is_set():
                        break
            finally:
                process.stdout.close()
                process.wait()
        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
        deadline = time.monotonic() + request.get("timeout_seconds", 45)
        result_status = None
        def poll():
            nonlocal result_status
            self.timer = None
            if self.process is not process:
                return
            for _ in range(50):
                try:
                    line = events.get_nowait()
                except queue.Empty:
                    break
                if line.startswith("EVENT:"):
                    event = json.loads(line[6:])
                    kind = event.get("type")
                    if kind == "text":
                        role = event.get("role", "assistant")
                        self.append(event.get("text", ""), role=role if role != self.current_role else None)
                    elif kind in ("notice", "partial"):
                        self.append(event.get("text", ""), role="notice")
                        if kind == "partial": result_status = "Finished with worker errors"
                    elif kind in ("error", "warning"):
                        self.append("\n" + event.get("message", "AI request failed.") + "\n", role="notice")
                        if event.get("status") == 429 and event.get("provider"):
                            self.cooldowns[event["provider"]] = time.monotonic() + max(1, event.get("retry_after", 30))
                        if kind == "error":
                            result_status = "Rate limited" if event.get("status") == 429 else "Request failed"
                elif line.startswith("MODELS:"):
                    self.models = json.loads(line[7:])
                    labels = [item["label"] for item in self.models]
                    self.lead.configure(values=labels)
                    self.lead.set(labels[0] if labels else "")
                    self.workers.delete(0, "end")
                    for label in labels:
                        self.workers.insert("end", label)
                    self.status.configure(text=f"{len(labels)} models available")
                elif line.startswith("HISTORY:") and history_key is not None:
                    self.histories.setdefault(history_key, []).extend(json.loads(line[8:]))
                    self.histories[history_key] = self.histories[history_key][-12:]
                else:
                    self.append(line,role="notice" if request["command"]=="list" else None)
            if process.poll() is None and time.monotonic() >= deadline:
                self.cancel()
                self.append("\n[Request timed out. Displayed output was kept.]\n", role="notice")
                self.status.configure(text="Timed out")
                return
            if process.poll() is not None and not reader_thread.is_alive() and events.empty():
                self.process = None
                self.set_running(False)
                self.status.configure(text=result_status or ("Finished" if process.returncode == 0 else "Request failed — see output"))
            else:
                self.timer = self.root.after(80, poll)
        self.timer = self.root.after(80, poll)
        return True

    def submit(self):
        if self.process is not None:
            return
        try:
            index = self.lead.current()
            key = (self.mode.get(), self.models[index]["provider"], self.models[index]["name"]) if index >= 0 else None
            request = build_request(self.mode.get(), self.command.get(), self.models, index,
                self.workers.curselection(), self.prompt.get("1.0", "end-1c"),
                list(self.attachments), self.histories.get(key, []),
                reply_tokens=None if self.reply_cap.get()=='Auto' else int(self.reply_cap.get()))
        except (ValueError, IndexError) as error:
            messagebox.showerror("AI request", str(error), parent=self.window)
            return
        for provider, model in [request["lead"], *request["workers"]]:
            remaining = self.cooldowns.get(provider, 0) - time.monotonic()
            if remaining > 0:
                messagebox.showinfo("Provider rate limit", f"{provider} is paused for about {int(remaining)+1} more seconds after a rate-limit response. Choose another provider or wait. The provider quota may last longer.", parent=self.window)
                return
        if request["history_omitted"]:
            self.append(f"\n[Fast context: {request['history_omitted']} older history messages omitted from this request.]\n", role="notice")
        self.append("\n" + self.mode.get() + " / " + self.command.get() + "\n", role="notice")
        self.append("You:\n" + request["prompt"] + "\n\n", role="user")
        self.append("Team output:\n" if request["command"] == "team" else "Assistant:\n", role="assistant")
        if self.start(request, key if request["command"] == "ask" else None):
            self.prompt.delete("1.0", "end")

    def add_attachment(self, name, content):
        candidate = "File: " + name + "\n" + content
        limits = FAST_LIMITS[self.mode.get()]
        maximum = limits["team_input_bytes"] if self.command.get() == "Team" else limits["input_bytes"]
        if len(self.attachments) >= limits["attachments"]:
            messagebox.showerror("Attachment limit", f"{self.mode.get()} fast mode allows {limits['attachments']} attachments.", parent=self.window)
            return False
        if input_size(self.prompt.get("1.0", "end-1c"), [*self.attachments, candidate]) > maximum:
            messagebox.showerror("Input limit", f"Task and attachments must fit within {maximum:,} UTF-8 bytes. Choose a smaller relevant excerpt.", parent=self.window)
            return False
        self.attachments.append(candidate)
        self.attachment_status()
        return True

    def attach_document(self):
        path = self.api.get_path()
        self.add_attachment(path.name if path else "untitled.txt", self.api.get_text())

    def attach_files(self):
        maximum = FAST_LIMITS[self.mode.get()]["input_bytes"]
        for filename in filedialog.askopenfilenames(parent=self.window):
            try:
                path = Path(filename)
                with path.open("rb") as stream:
                    data = stream.read(maximum + 1)
                if len(data) > maximum:
                    raise ValueError(f"{path.name} exceeds {maximum:,} bytes. Attach a smaller relevant excerpt.")
                self.add_attachment(path.name, data.decode("utf-8"))
            except (OSError, UnicodeError, ValueError) as error:
                messagebox.showerror("Attachment", str(error), parent=self.window)

    def clear_attachments(self):
        self.attachments.clear()
        self.attachment_status()

    def prompt_changed(self, event=None):
        if self.prompt.edit_modified():
            self.prompt.edit_modified(False)
            self.attachment_status()
            self.update_placeholder()
            lines=self.prompt.get("1.0","end-1c").count("\n")+1
            self.prompt.configure(height=max(2,min(5,lines)))

    def attachment_status(self):
        limits = dict(FAST_LIMITS[self.mode.get()])
        if hasattr(self,'reply_cap') and self.reply_cap.get()!='Auto':
            try: limits['reply_tokens']=int(self.reply_cap.get())
            except ValueError: pass
            limits['empty_retry_tokens']=max(limits['reply_tokens'],limits['empty_retry_tokens'])
        team = self.command.get() == "Team"
        maximum = limits["team_input_bytes"] if team else limits["input_bytes"]
        timeout = limits["team_timeout_seconds"] if team else limits["timeout_seconds"]
        used = input_size(self.prompt.get("1.0", "end-1c"), self.attachments)
        self.limits_label.configure(text=f"Fast {self.mode.get()} · Input {used:,}/{maximum:,} bytes · Reply ≤{limits['reply_tokens']:,} tokens · "
            + (f"Up to {limits['workers']} workers" if team else f"Last {limits['history_turns']} turns / {limits['history_bytes']//1024} KiB history")
            + f" · {timeout}s timeout · Empty reply: 1 retry ≤{limits['empty_retry_tokens']:,} tokens")
        size = sum(len(text.encode("utf-8")) for text in self.attachments)
        self.attached.configure(text=f"{len(self.attachments)}/{limits['attachments']} files · {size:,} bytes · Included with each message until removed")
        for child in self.chips.winfo_children(): child.destroy()
        self.chip_buttons=[]
        for index,attachment in enumerate(self.attachments):
            name=attachment.split("\n",1)[0].removeprefix("File: ")
            label=name if len(name)<=25 else name[:22]+"…"
            button=ttk.Button(self.chips,text=label+"  ×",style='Ai.Chip.TButton',
                              command=lambda index=index:self.remove_attachment(index))
            self.chip_buttons.append(button)
        self.layout_chips()
        if self.attachments: self.chips.grid()
        else: self.chips.grid_remove()

    def layout_chips(self):
        available=max(200,self.chips.winfo_width())
        x=y=0
        height=max((button.winfo_reqheight() for button in self.chip_buttons),default=1)
        for button in self.chip_buttons:
            width=min(available,button.winfo_reqwidth())
            if x and x+width>available:
                x=0; y+=height+6
            button.place(x=x,y=y,width=width,height=height)
            x+=width+6
        self.chips.configure(height=y+height)

    def remove_attachment(self,index):
        if 0<=index<len(self.attachments):
            self.attachments.pop(index); self.attachment_status()

    def copy_conversation(self):
        """Copy the retained visible transcript, not hidden history/attachments."""
        transcript = self.output.get("1.0", "end-1c")
        if not transcript.strip():
            messagebox.showinfo("Copy conversation", "There is no conversation to copy yet.",
                                parent=self.window)
            return False
        try:
            # Own the clipboard on the editor root so closing this window keeps it.
            self.root.clipboard_clear()
            self.root.clipboard_append(transcript)
        except tk.TclError as error:
            messagebox.showerror("Copy conversation", str(error), parent=self.window)
            return False
        if self.process is None:
            self.status.configure(text="Conversation copied")
        return True

    def reset(self):
        if self.process is None:
            self.histories.clear()
            self.append("Conversation context reset. Previous messages remain available to copy.\n", role="notice")

    def help(self):
        messagebox.showinfo("AI Assistant", "Choose Chat or Code, then Ask or Team.\n"
            "Enter sends; Shift+Enter adds a new line. Remove files using their × chips.\n"
            "Ask: select one model and enter a question.\n"
            "Team: choose a lead, workers from different developers, and a task.\n"
            "Only explicitly attached documents are sent. Replies never modify the editor.\n"
            "The AI cannot browse project files, write files, or run commands/tests.\n"
            "Direct project work needs project-scoped file tools and an edit preview/apply feature.\n"
            "Copy conversation copies the retained visible prompts and output (including team output).\n"
            "Older trimmed output and hidden attachment text are not included.\n"
            "Fast Chat: 4 KiB input, 512 reply tokens, 2 recent turns, 60s timeout.\n"
            "Fast Code: 12 KiB input (6 KiB for Team), 1,536 reply tokens, 3 turns, 120s timeout.\n"
            "Teams allow 2 workers; timeouts are 120s Chat / 180s Code.\n"
            "Older request history is omitted with a notice. Input/files are rejected if too large.\n"
            "An empty reply is retried once with up to 2,048 Chat / 3,072 Code tokens.\n"
            "HTTP 429 is not automatically retried; its provider is briefly paused.\n"
            "Reply caps may leave large answers incomplete. Actual speed depends on the provider/model.\n"
            "Refresh models updates the list; Reset clears solo histories.\n"
            "Configure shared API keys and Free plan confirmations using /keys in chat_ai.py or code_ai.py.", parent=self.window)

    def cancel(self):
        if self.process is not None:
            self.stop.set()
            self.process.kill()
            self.process.wait(timeout=5)
            self.process = None
            self.set_running(False)
            self.status.configure(text="Cancelled")
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None

    def close(self):
        self.cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def cleanup(self):
        self.close()


def register(api):
    assistant = Assistant(api)
    api.add_command("AI Chat…", lambda: assistant.open("Chat"))
    api.add_command("AI Code…", lambda: assistant.open("Code"))
