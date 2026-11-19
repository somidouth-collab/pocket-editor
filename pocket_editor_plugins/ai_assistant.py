"""AI Assistant: Chat/Code with explicit provider configuration. See docs/AI_ASSISTANT.md."""
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_ATTACHMENTS = 4

SETTINGS_PATH = Path(__file__).with_name('ai_assistant_settings.json')
PROTOCOLS = ('Chat Completions', 'OpenAI Responses', 'Google Gemini')
PRESETS = {
    'OpenAI': ('OpenAI Responses', 'https://api.openai.com/v1/responses'),
    'Google Gemini': ('Google Gemini', 'https://generativelanguage.googleapis.com/v1beta'),
    'Ollama': ('Chat Completions', 'http://127.0.0.1:11434/v1/chat/completions'),
    'OpenRouter': ('Chat Completions', 'https://openrouter.ai/api/v1/chat/completions'),
    'Groq': ('Chat Completions', 'https://api.groq.com/openai/v1/chat/completions'),
    'DeepSeek': ('Chat Completions', 'https://api.deepseek.com/chat/completions'),
}
KEY_VARIABLES = {'OpenAI': 'OPENAI_API_KEY', 'Google Gemini': 'GEMINI_API_KEY',
                 'Ollama': 'OLLAMA_API_KEY', 'OpenRouter': 'OPENROUTER_API_KEY',
                 'Groq': 'GROQ_API_KEY', 'DeepSeek': 'DEEPSEEK_API_KEY', 'Custom': 'CUSTOM_AI_API_KEY'}
PROVIDER_HELP = {
    'OpenAI': 'Use an OpenAI Platform API key. Refresh models and select a Responses-compatible text model. ChatGPT subscriptions do not supply API credits.',
    'Google Gemini': '1. Create a Gemini API key at aistudio.google.com/apikey. 2. Paste it here. 3. Refresh models and choose a text model. 4. Save, close Settings, and Send. The endpoint stays at /v1beta; the plugin adds the model URL.',
    'Ollama': 'Install/start Ollama on this computer, download a model with ollama pull MODEL, then Refresh models. Local Ollama normally needs no API key. Use the exact downloaded model name.',
    'OpenRouter': 'Use an OpenRouter API key, not an OpenAI key. Refresh models and select the full provider/model ID. Access and credits depend on your OpenRouter account.',
    'Groq': 'Create a Groq API key in the Groq console. Refresh models and select an available text model. Use the Groq endpoint, not the OpenAI endpoint.',
    'DeepSeek': 'Create a DeepSeek API key in the DeepSeek platform. Refresh models or enter an exact current model ID. API credits are separate from website chat access.',
    'Custom': 'Choose the provider’s API format and full endpoint. Only OpenAI-compatible chat/Responses and the native Gemini API are supported. Other native APIs need their own adapter.',
}


def provider_for(config):
    endpoint = config.get('endpoint', '').rstrip('/')
    return next((name for name, (protocol, url) in PRESETS.items()
                 if endpoint == url.rstrip('/') and config.get('protocol') == protocol), 'Custom')


DEFAULTS = dict(protocol='Chat Completions', reasoning='default', endpoint='http://127.0.0.1:11434/v1/chat/completions', model='',
                reply_tokens=1024, input_bytes=65536, timeout=60, session_requests=20)
SYSTEM_PROMPTS = {'Chat': 'You are a helpful assistant. Be clear and concise.',
                  'Code': 'Help write and review code. Provide focused examples. Never claim to have executed code or edited files.'}


def validate_config(values):
    result = {name: values.get(name, default) for name, default in DEFAULTS.items()}
    if result['protocol'] not in PROTOCOLS or result['reasoning'] not in ('default', 'low', 'medium', 'high'):
        raise ValueError('Choose an API format and a supported reasoning setting.')
    if not isinstance(result['endpoint'], str) or not isinstance(result['model'], str):
        raise ValueError('Endpoint and model ID must be text.')
    result['endpoint'], result['model'] = result['endpoint'].strip(), result['model'].strip()
    url = urlsplit(result['endpoint'])
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        raise ValueError('Use a complete API URL without credentials, query, or fragment.')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Use HTTPS for remote providers; HTTP is allowed only for local loopback servers.')
    if result['protocol'] == 'OpenAI Responses' and not url.path.rstrip('/').endswith('/responses'):
        raise ValueError('Responses format needs a complete endpoint ending in /responses.')
    if result['protocol'] == 'Google Gemini':
        if not url.path.rstrip('/').endswith(('/v1beta', '/v1')):
            raise ValueError('Gemini needs its API base URL, for example https://generativelanguage.googleapis.com/v1beta.')
        result['model'] = result['model'].removeprefix('models/')
        if not re.fullmatch(r'[A-Za-z0-9._-]+', result['model']):
            raise ValueError('Choose a Gemini model ID from Refresh models; do not paste a model URL.')
    if result['protocol'] == 'Chat Completions' and not url.path.rstrip('/').endswith('/chat/completions'):
        raise ValueError('Chat Completions needs the full endpoint ending in /chat/completions. Select a provider preset to fill it.')
    if not result['model'] or len(result['model']) > 200:
        raise ValueError('Enter the provider’s exact model ID.')
    for name, minimum, maximum in (('reply_tokens', 64, 8192), ('input_bytes', 1024, 1048576),
                                   ('timeout', 5, 180), ('session_requests', 1, 1000)):
        try:
            value = int(result[name])
        except (ValueError, TypeError):
            raise ValueError(f'{name} must be a whole number.') from None
        if not minimum <= value <= maximum:
            raise ValueError(f'{name} must be between {minimum} and {maximum}.')
        result[name] = value
    return result


def build_messages(config, mode, prompt, context='', history=()):
    if mode not in SYSTEM_PROMPTS:
        raise ValueError('Choose Chat or Code.')
    if not prompt.strip():
        raise ValueError('Enter a message.')
    text = prompt.strip() + ('\n\nAttached context:\n' + context if context else '')
    messages = [{'role': 'system', 'content': SYSTEM_PROMPTS[mode]},
                *[dict(item) for item in history[-4:]], {'role': 'user', 'content': text}]
    size = sum(len(item['content'].encode('utf-8')) for item in messages)
    if size > config['input_bytes']:
        raise ValueError(f'Input and history use {size:,} bytes; limit is {config["input_bytes"]:,}. Reset history, shorten the prompt, or raise the limit.')
    return messages


def read_attachment(path, limit):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('Choose a regular UTF-8 text or code file.')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'{path.name} exceeds the input-byte limit.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError(f'{path.name} is not UTF-8 text. Images, PDFs, and binary files are not supported.') from None
    if text.startswith('%PDF-') or any(ord(char) < 32 and char not in '\t\r\n' for char in text):
        raise ValueError(f'{path.name} is a binary or unsupported document. Choose text or code.')
    return {'path': path, 'text': text}


def attachment_context(attachments):
    return '\n\n'.join(f'--- File: {item["path"].parent.name}/{item["path"].name} ---\n{item["text"]}' for item in attachments)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None  # Do not forward provider credentials to a redirected URL.


class AIResponseError(ValueError):
    """Only locally authored, non-secret error descriptions belong here."""


def request_json(config, key, url, payload=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if key:
        if config.get('protocol') == 'Google Gemini':
            headers['x-goog-api-key'] = key
        else:
            headers['Authorization'] = 'Bearer ' + key
    request = Request(url, data=json.dumps(payload).encode('utf-8') if payload is not None else None, headers=headers)
    with build_opener(NoRedirects()).open(request, timeout=config['timeout']) as response:
        raw = response.read(2097153)
    if len(raw) > 2097152:
        raise AIResponseError('Provider response exceeds the 2 MiB response limit.')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise AIResponseError('Provider returned an unexpected JSON format.')
    return data


def send_request(config, key, messages):
    if config.get('protocol') == 'Google Gemini':
        contents = [{'role': 'model' if item['role'] == 'assistant' else 'user',
                     'parts': [{'text': item['content']}]} for item in messages[1:]]
        payload = {'systemInstruction': {'parts': [{'text': messages[0]['content']}]},
                   'contents': contents, 'generationConfig': {'maxOutputTokens': config['reply_tokens']}}
        endpoint = config['endpoint'].rstrip('/') + '/models/' + quote(config['model'], safe='') + ':generateContent'
        data = request_json(config, key, endpoint, payload)
        candidates = data.get('candidates')
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise AIResponseError('Gemini returned no text candidate. Check model access, safety restrictions, and output limit; no retry was sent.')
        candidate = candidates[0]
        parts = candidate.get('content', {}).get('parts', [])
        reply = '\n'.join(part['text'] for part in parts if isinstance(part, dict)
                          and isinstance(part.get('text'), str) and not part.get('thought'))
        if not reply.strip():
            raise AIResponseError('Gemini returned no visible text. Check safety restrictions or raise the reply-token cap for reasoning models. No retry was sent.')
        usage_data = data.get('usageMetadata') or {}
        usage = {'incomplete': candidate.get('finishReason') not in (None, 'STOP')}
        if isinstance(usage_data, dict) and 'totalTokenCount' in usage_data:
            usage['total_tokens'] = usage_data['totalTokenCount']
        return reply, usage
    responses = config.get('protocol') == 'OpenAI Responses'
    if responses:
        payload = {'model': config['model'], 'instructions': messages[0]['content'],
                   'input': messages[1:], 'max_output_tokens': config['reply_tokens'],
                   'stream': False, 'store': False}
        if config.get('reasoning', 'default') != 'default':
            payload['reasoning'] = {'effort': config['reasoning']}
    else:
        payload = {'model': config['model'], 'messages': messages,
                   'max_tokens': config['reply_tokens'], 'stream': False}
    data = request_json(config, key, config['endpoint'], payload)
    usage = dict(data['usage']) if isinstance(data.get('usage'), dict) else {}
    if responses:
        if data.get('error') or data.get('status') in ('failed', 'cancelled'):
            raise AIResponseError('OpenAI could not complete the response. Check model access and provider limits.')
        parts = []
        for item in data.get('output', []):
            if isinstance(item, dict) and item.get('type') == 'message':
                for part in item.get('content', []):
                    if isinstance(part, dict):
                        value = part.get('text') if part.get('type') == 'output_text' else part.get('refusal') if part.get('type') == 'refusal' else None
                        if isinstance(value, str):
                            parts.append(value)
        reply = '\n'.join(parts)
        usage['incomplete'] = data.get('status') == 'incomplete'
    else:
        try:
            reply = data['choices'][0]['message']['content']
            usage['incomplete'] = data['choices'][0].get('finish_reason') == 'length'
        except (KeyError, IndexError, TypeError):
            raise AIResponseError('Provider did not return a chat-completions text reply.') from None
    if not isinstance(reply, str) or not reply.strip():
        raise AIResponseError('No visible text returned. The output cap may have been consumed by reasoning; increase it explicitly or choose another model. No retry was sent.')
    return reply, usage


def list_models(config, key):
    if config.get('protocol') == 'Google Gemini':
        data = request_json(config, key, config['endpoint'].rstrip('/') + '/models')
        if not isinstance(data.get('models'), list):
            raise AIResponseError('Gemini did not return a model list. Check the key and API base URL, or enter a model ID manually.')
        return sorted({item['name'].removeprefix('models/') for item in data['models']
                       if isinstance(item, dict) and isinstance(item.get('name'), str)
                       and 'generateContent' in item.get('supportedGenerationMethods', [])})
    endpoint = config['endpoint'].rstrip('/')
    suffix = '/responses' if config.get('protocol') == 'OpenAI Responses' else '/chat/completions'
    if not endpoint.endswith(suffix):
        raise AIResponseError('Cannot derive a models URL from this endpoint. Enter the model ID manually.')
    data = request_json(config, key, endpoint[:-len(suffix)] + '/models')
    if not isinstance(data.get('data'), list):
        raise AIResponseError('Provider did not return a models list. Enter the model ID manually.')
    return sorted({item['id'] for item in data['data'] if isinstance(item, dict) and isinstance(item.get('id'), str)})


class AIAssistant:
    def __init__(self, api):
        self.api, self.editor = api, api._editor
        self.root, self.window = self.editor.root, None
        self.config = DEFAULTS.copy()
        try:
            settings_source = SETTINGS_PATH if SETTINGS_PATH.exists() else SETTINGS_PATH.with_name('my_ai_example_settings.json')
            saved = json.loads(settings_source.read_text(encoding='utf-8'))
            self.config.update(validate_config(saved))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.histories = {}
        self.provider_keys = {}
        self.profiles = {}
        self.pending_prompt = None
        self.used, self.busy, self.pause_until = 0, False, 0
        self.last_reply = ''
        self.attachments = []
        self.attachment_buttons = []
        self.results = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.poll_job = self.root.after(100, self.poll)
        api.on_cleanup(self.cleanup)
        api.add_command('AI Assistant…', self.show)
        api.provide_service('get_status', self.get_status)

    def get_status(self):
        return {'busy': self.busy, 'requests_used': self.used, 'request_limit': self.config['session_requests'],
                'cooldown_seconds': max(0, int(self.pause_until - time.monotonic()))}

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify(); self.window.lift()
            return
        window = self.window = tk.Toplevel(self.root)
        window.title('AI Assistant — Chat / Code')
        window.geometry('980x780'); window.minsize(640, 540)
        window.protocol('WM_DELETE_WINDOW', self.hide)
        self.fonts = {name: self.editor.ui_font.copy() for name in ('body', 'title', 'label', 'mono')}
        self.fonts['body'].configure(size=11)
        self.fonts['title'].configure(size=16, weight='bold')
        self.fonts['label'].configure(size=10, weight='bold')
        self.fonts['mono'] = self.editor.editor_font.copy()
        style = ttk.Style(window)
        for name, color in (('MyAI.TFrame', '#1e1e1e'), ('MyAI.Composer.TFrame', '#2d2d2d')):
            style.configure(name, background=color)
        style.configure('MyAI.TLabel', background='#1e1e1e', foreground='#cccccc', font=self.fonts['body'])
        style.configure('MyAI.Title.TLabel', background='#1e1e1e', foreground='#eeeeee', font=self.fonts['title'])
        style.configure('MyAI.Muted.TLabel', background='#1e1e1e', foreground='#969696', font=self.editor.ui_font)
        style.configure('MyAI.TCheckbutton', background='#2d2d2d', foreground='#cccccc')
        style.map('MyAI.TCheckbutton', background=[('active', '#2d2d2d')])
        window.configure(background='#1e1e1e')
        body = ttk.Frame(window, padding=(22, 16), style='MyAI.TFrame')
        body.pack(fill='both', expand=True)
        header = ttk.Frame(body, style='MyAI.TFrame'); header.pack(fill='x')
        ttk.Label(header, text='AI Assistant', style='MyAI.Title.TLabel').pack(side='left')
        self.mode = tk.StringVar(window, 'Chat')
        ttk.Combobox(header, textvariable=self.mode, values=('Chat', 'Code'), state='readonly', width=7).pack(side='left', padx=12)
        ttk.Button(header, text='How to use', command=self.show_help).pack(side='right', padx=8)
        ttk.Button(header, text='Settings', command=self.show_settings).pack(side='right')
        ttk.Button(header, text='Reset context', command=self.reset).pack(side='right', padx=8)
        self.model_summary = ttk.Label(body, text='', style='MyAI.Muted.TLabel')
        self.model_summary.pack(anchor='w', pady=(6, 12))
        ttk.Separator(body).pack(fill='x')

        # Provider credentials and limits live in a separate, non-modal settings window.
        self.settings_window = settings = tk.Toplevel(window)
        settings.title('AI Assistant · Provider settings'); settings.geometry('760x610'); settings.minsize(640, 560)
        settings.transient(window); settings.protocol('WM_DELETE_WINDOW', settings.withdraw)
        settings.withdraw()
        settings_body = ttk.Frame(settings, padding=18); settings_body.pack(fill='both', expand=True)
        ttk.Label(settings_body, text='Provider & limits', style='Title.TLabel').pack(anchor='w', pady=(0, 12))
        fields = ttk.Frame(settings_body); fields.pack(fill='x'); fields.columnconfigure(1, weight=1)
        self.values = {name: tk.StringVar(window, str(value)) for name, value in self.config.items()}
        initial_provider = provider_for(self.config)
        self.key = tk.StringVar(window, os.environ.get(KEY_VARIABLES[initial_provider], ''))
        self.key_host = urlsplit(self.config['endpoint']).hostname
        self.active_provider = initial_provider
        provider_row = ttk.Frame(settings_body)
        provider_row.pack(fill='x', before=fields, pady=(0, 10))
        ttk.Label(provider_row, text='Provider').pack(side='left', padx=(0, 8))
        self.provider = tk.StringVar(window, initial_provider)
        preset = ttk.Combobox(provider_row, textvariable=self.provider, values=(*PRESETS, 'Custom'), state='readonly', width=14)
        preset.pack(side='left'); preset.bind('<<ComboboxSelected>>', lambda event: self.apply_preset())
        ttk.Label(provider_row, text='API format').pack(side='left', padx=8)
        ttk.Combobox(provider_row, textvariable=self.values['protocol'], values=PROTOCOLS, state='readonly', width=20).pack(side='left')
        for row, (label, variable, secret) in enumerate((('API endpoint', self.values['endpoint'], False),
                ('Model ID', self.values['model'], False), ('API key', self.key, True))):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=5)
            if row == 1:
                self.model_picker = ttk.Combobox(fields, textvariable=variable)
                self.model_picker.grid(row=row, column=1, sticky='ew', pady=5)
            else:
                ttk.Entry(fields, textvariable=variable, show='•' if secret else '').grid(row=row, column=1, sticky='ew', pady=5)
        model_controls = ttk.Frame(settings_body); model_controls.pack(fill='x', pady=10)
        self.models_button = ttk.Button(model_controls, text='Refresh models', command=self.refresh_models)
        self.models_button.pack(side='left')
        ttk.Label(model_controls, text='Reasoning (Responses)').pack(side='left', padx=8)
        ttk.Combobox(model_controls, textvariable=self.values['reasoning'], values=('default', 'low', 'medium', 'high'), state='readonly', width=9).pack(side='left')
        limits = ttk.Frame(settings_body); limits.pack(fill='x', pady=10)
        for column, (label, name) in enumerate((('Reply tokens', 'reply_tokens'), ('Input bytes', 'input_bytes'),
                                                ('Timeout (s)', 'timeout'), ('Requests/session', 'session_requests'))):
            limits.columnconfigure(column, weight=1)
            ttk.Label(limits, text=label).grid(row=0, column=column, sticky='w', padx=(0, 10))
            ttk.Entry(limits, textvariable=self.values[name], width=11).grid(row=1, column=column, sticky='ew', padx=(0, 10), pady=4)
        self.provider_tip = ttk.Label(settings_body, text=PROVIDER_HELP[initial_provider], wraplength=680, style='Muted.TLabel')
        self.provider_tip.pack(fill='x', pady=6)
        self.values['endpoint'].trace_add('write', self.endpoint_changed)
        ttk.Label(settings_body, text='API keys stay in memory. Request limits are separate from provider billing.',
                  style='Muted.TLabel', wraplength=640).pack(anchor='w', pady=8)
        actions = ttk.Frame(settings_body); actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Save settings', command=self.save_settings, style='Accent.TButton').pack(side='right')
        ttk.Button(actions, text='Done', command=settings.withdraw).pack(side='right', padx=8)
        self.settings_status = ttk.Label(settings_body, text='', style='Muted.TLabel', wraplength=640)
        self.settings_status.pack(anchor='w')

        conversation = ttk.Frame(body, style='MyAI.TFrame')
        conversation.pack(fill='both', expand=True, pady=(12, 16))
        self.output = ScrolledText(conversation, height=6, width=1, state='disabled', wrap='word',
            background='#1e1e1e', foreground='#cccccc', selectbackground='#264f78',
            borderwidth=0, highlightthickness=0, padx=16, pady=14, font=self.fonts['body'], spacing3=8)
        self.output.pack(fill='both', expand=True)
        self.output.frame.configure(background='#1e1e1e')
        self.output.vbar.configure(background='#424242', troughcolor='#1e1e1e', borderwidth=0, width=9)
        self.output.tag_configure('user', background='#2a2d2e', lmargin1=100, lmargin2=100, rmargin=14, spacing1=14, spacing3=12)
        self.output.tag_configure('assistant', lmargin1=16, lmargin2=16, rmargin=28, spacing1=14, spacing3=12)
        self.output.tag_configure('notice', foreground='#969696', lmargin1=16, lmargin2=16)
        self.output.tag_configure('role', font=self.fonts['label'], foreground='#3794ff')
        self.output.tag_configure('code', font=self.fonts['mono'], background='#181818', foreground='#d4d4d4')
        self.output.tag_raise('sel')
        self.welcome = ttk.Frame(conversation, style='MyAI.TFrame', padding=16)
        self.welcome.place(relx=.5, rely=.45, anchor='center')
        ttk.Label(self.welcome, text='What would you like to build?', style='MyAI.Title.TLabel').pack(pady=(0, 8))
        ttk.Label(self.welcome, text='A conversation, a code review, or your next plugin.', style='MyAI.Muted.TLabel').pack(pady=(0, 18))
        examples = ttk.Frame(self.welcome, style='MyAI.TFrame'); examples.pack()
        for label, prompt in (('Explain code', 'Explain the code I attach.'),
                              ('Build a plugin', 'Help me build a Pocket Editor plugin.'),
                              ('Review a change', 'Review my attached code for bugs.')):
            ttk.Button(examples, text=label, command=lambda prompt=prompt: self.use_example(prompt)).pack(side='left', padx=4)
        self.composer = tk.Frame(body, background='#2d2d2d', highlightbackground='#3c3c3c', highlightcolor='#007acc', highlightthickness=1)
        self.composer.pack(fill='x')
        self.attachment_strip = ttk.Frame(self.composer, padding=(10, 6, 10, 0), style='MyAI.Composer.TFrame')
        self.attachment_strip.pack(fill='x')
        self.attachment_strip.bind('<Configure>', lambda event: self.layout_attachments())
        ttk.Label(self.composer, text='Message', background='#2d2d2d', foreground='#969696').pack(anchor='w', padx=14, pady=(10, 0))
        self.prompt = tk.Text(self.composer, height=4, width=1, wrap='word', background='#2d2d2d', foreground='#dddddd',
            insertbackground='#3794ff', selectbackground='#264f78', borderwidth=0, highlightthickness=0,
            padx=14, pady=8, font=self.fonts['body'], undo=True)
        self.prompt.pack(fill='x')
        self.prompt.bind('<Return>', self.send_enter)
        self.prompt.bind('<Control-Return>', lambda event: self.submit() or 'break')
        self.prompt.bind('<FocusIn>', lambda event: self.composer.configure(highlightbackground='#007acc'))
        self.prompt.bind('<FocusOut>', lambda event: self.composer.configure(highlightbackground='#3c3c3c'))
        composer_controls = ttk.Frame(self.composer, padding=(10, 0, 10, 10), style='MyAI.Composer.TFrame')
        composer_controls.pack(fill='x')
        self.attach = tk.BooleanVar(window, False)
        ttk.Button(composer_controls, text='+ Files', command=self.attach_files).pack(side='left', padx=(0, 8))
        ttk.Checkbutton(composer_controls, text='Attach current tab', variable=self.attach, style='MyAI.TCheckbutton').pack(side='left')
        self.send = ttk.Button(composer_controls, text='Send ↑', style='Accent.TButton', command=self.submit, state='disabled' if self.busy else 'normal')
        self.send.pack(side='right')
        self.status = ttk.Label(body, text='Ready · configure your provider in Settings', style='MyAI.Muted.TLabel', wraplength=600)
        self.status.pack(fill='x', pady=(8, 6))
        footer = ttk.Frame(body, style='MyAI.TFrame'); footer.pack(fill='x')
        ttk.Label(footer, text='Enter to send · Shift+Enter for a new line', style='MyAI.Muted.TLabel').pack(side='left')
        ttk.Button(footer, text='Open reply', command=self.open_reply).pack(side='right')
        ttk.Button(footer, text='Copy conversation', command=self.copy_conversation).pack(side='right', padx=8)
        for variable in (self.values['model'], self.provider, self.mode):
            variable.trace_add('write', self.update_summary)
        window.bind('<Configure>', self.resize_chat)
        self.update_summary()
        self.update_attachments()

    def attach_files(self):
        names = filedialog.askopenfilenames(parent=self.window, title='Attach text or code files',
            filetypes=[('Text and code', '*.txt *.md *.py *.json *.js *.ts *.html *.css *.cpp *.h *.php *.csv *.yaml *.yml'), ('All files', '*')])
        if not names:
            return
        try:
            limit = int(self.values['input_bytes'].get())
            if not 1024 <= limit <= 1048576:
                raise ValueError('Set an input-byte limit between 1,024 and 1,048,576 in Settings.')
            additions = []
            known = {item['path'] for item in self.attachments}
            for name in names:
                path = Path(name).resolve()
                if path not in known:
                    if len(self.attachments) + len(additions) >= MAX_ATTACHMENTS:
                        raise ValueError(f'Attach at most {MAX_ATTACHMENTS} files. Remove a chip to choose another.')
                    additions.append(read_attachment(path, limit))
                    known.add(path)
            combined = [*self.attachments, *additions]
            if len(attachment_context(combined).encode('utf-8')) > limit:
                raise ValueError('Selected files and their headers exceed the input-byte limit. Use smaller files or raise it in Settings.')
        except (OSError, ValueError) as error:
            messagebox.showerror('Attach files', str(error), parent=self.window)
            return
        self.attachments = combined
        self.update_attachments()
        self.status.configure(text=f'{len(self.attachments)}/{MAX_ATTACHMENTS} files attached · snapshots included with each message until removed')

    def remove_attachment(self, item):
        if item in self.attachments:
            self.attachments.remove(item)
            self.update_attachments()
            self.status.configure(text='Attachment removed · Reset context to clear files sent in earlier messages')

    def update_attachments(self):
        for button in self.attachment_buttons:
            button.destroy()
        self.attachment_buttons = []
        for item in self.attachments:
            name = item['path'].name
            label = name if len(name) <= 24 else name[:21] + '…'
            button = ttk.Button(self.attachment_strip, text=label + '  ×', command=lambda item=item: self.remove_attachment(item))
            self.attachment_buttons.append(button)
        self.layout_attachments()

    def layout_attachments(self):
        available = max(250, self.attachment_strip.winfo_width() - 20)
        used = row = column = 0
        for button in self.attachment_buttons:
            width = button.winfo_reqwidth() + 6
            if used and used + width > available:
                row += 1
                column = used = 0
            button.grid(row=row, column=column, sticky='w', padx=(0, 6), pady=(0, 4))
            used += width
            column += 1

    def hide(self):
        self.settings_window.withdraw()
        self.window.withdraw()

    def show_settings(self):
        self.settings_window.deiconify()
        self.settings_window.lift()

    def update_summary(self, *args):
        model = self.values['model'].get().strip() or 'Choose a model in Settings'
        self.model_summary.configure(text=f'{self.provider.get()}  /  {model}  ·  {self.mode.get()}')

    def resize_chat(self, event):
        if event.widget is self.window:
            width = max(300, self.window.winfo_width() - 80)
            self.status.configure(wraplength=width)
            self.output.tag_configure('user', lmargin1=max(30, min(160, width // 6)), lmargin2=max(30, min(160, width // 6)))

    def use_example(self, prompt):
        if not self.busy:
            self.prompt.delete('1.0', 'end'); self.prompt.insert('1.0', prompt); self.prompt.focus_set()

    def send_enter(self, event):
        if event.state & 1:
            return None
        self.submit()
        return 'break'

    def copy_conversation(self):
        text = self.output.get('1.0', 'end-1c')
        if text:
            try:
                self.root.clipboard_clear(); self.root.clipboard_append(text)
                self.status.configure(text='Conversation copied')
            except tk.TclError:
                self.status.configure(text='Could not access the clipboard')

    def endpoint_changed(self, *_):
        try:
            host = urlsplit(self.values['endpoint'].get().strip()).hostname
        except ValueError:
            host = None
        if host != self.key_host:
            self.key.set('')
            self.key_host = host

    def apply_preset(self):
        previous = self.active_provider
        self.provider_keys[previous] = self.key.get()
        self.profiles[previous] = {name: value.get() for name, value in self.values.items()}
        selected = self.provider.get()
        self.active_provider = selected
        preset = PRESETS.get(selected)
        if preset:
            protocol, endpoint = preset
            profile = self.profiles.get(selected, dict(self.config, protocol=protocol, endpoint=endpoint, model='', reasoning='default'))
            for name, value in profile.items():
                self.values[name].set(str(value))
        else:
            self.key.set('')
        self.key.set(self.provider_keys.get(selected, os.environ.get(KEY_VARIABLES[selected], '')))
        self.key_host = urlsplit(self.values['endpoint'].get()).hostname
        self.model_picker.configure(values=())
        self.histories.clear()
        self.provider_tip.configure(text=PROVIDER_HELP[selected])
        self.status.configure(text='Choose a provider, enter its own API key, then Refresh models. See How to use for Gemini and local Ollama examples.')
        self.update_summary()

    def checked_key(self, config):
        self.endpoint_changed()
        key = self.key.get().strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in key):
            raise ValueError('API key must be a single line without control characters.')
        host = urlsplit(config['endpoint']).hostname
        known_remote = {'api.openai.com', 'generativelanguage.googleapis.com', 'openrouter.ai', 'api.groq.com', 'api.deepseek.com'}
        if host in known_remote and not key:
            raise ValueError('Enter this provider’s own API key or set ' + KEY_VARIABLES.get(provider_for(config), 'CUSTOM_AI_API_KEY') + '. Website/chat login credentials are not API keys.')
        return key

    def show_help(self):
        help_window = tk.Toplevel(self.window)
        help_window.title('AI Assistant — setup and Gemini example')
        help_window.geometry('760x650')
        text = ScrolledText(help_window, wrap='word', padx=16, pady=16)
        text.pack(fill='both', expand=True)
        guide = Path(__file__).resolve().parents[1] / 'docs/AI_ASSISTANT.md'
        try:
            content = guide.read_text(encoding='utf-8')
        except OSError:
            content = PROVIDER_HELP['Google Gemini'] + '\n\n' + '\n\n'.join(name + ': ' + tip for name, tip in PROVIDER_HELP.items())
        text.insert('1.0', content)
        text.configure(state='disabled')

    def refresh_models(self):
        if self.busy:
            return
        try:
            values = {name: variable.get() for name, variable in self.values.items()}
            config = validate_config(dict(values, model=values['model'] or 'catalog-only'))
            key = self.checked_key(config)
            if self.used >= config['session_requests'] or time.monotonic() < self.pause_until:
                raise ValueError('Request cap or rate-limit pause reached. Wait or raise the session cap explicitly.')
        except ValueError as error:
            messagebox.showerror('AI Assistant models', str(error), parent=self.window)
            return
        self.config, self.busy = config, True
        self.used += 1
        self.send.configure(state='disabled'); self.models_button.configure(state='disabled')
        self.status.configure(text='Loading model catalog…')
        threading.Thread(target=self.worker, args=(config, key, None, (config['endpoint'], config['protocol'])), daemon=True).start()

    def current_config(self):
        return validate_config({name: variable.get() for name, variable in self.values.items()})

    def save_settings(self):
        try:
            config = self.current_config()
            self.editor.write_data(SETTINGS_PATH, json.dumps(config, indent=2).encode('utf-8'))
        except (ValueError, OSError) as error:
            messagebox.showerror('AI Assistant settings', str(error), parent=self.window)
            return
        self.config = config
        self.status.configure(text='Settings saved · API key is not stored')
        self.settings_status.configure(text='Settings saved · API key is not stored')

    def append(self, text):
        self.welcome.place_forget()
        role = 'user' if text.startswith('You:\n') else 'assistant' if text.startswith('Assistant:\n') else 'notice'
        self.output.configure(state='normal')
        start = self.output.index('end-1c')
        self.output.insert('end', text, (role,))
        if role != 'notice':
            self.output.tag_add('role', start, f'{start} lineend')
        code = False
        for line, value in enumerate(text.splitlines()):
            if value.lstrip().startswith('```'):
                code = not code
            elif code:
                self.output.tag_add('code', f'{start} +{line}lines linestart', f'{start} +{line}lines lineend')
        self.output.tag_raise('sel')
        self.output.see('end'); self.output.configure(state='disabled')

    def submit(self):
        if self.busy:
            return
        try:
            config = self.current_config()
            if self.used >= config['session_requests']:
                raise ValueError('Session request limit reached. Raise the limit explicitly to send more.')
            if time.monotonic() < self.pause_until:
                raise ValueError('Provider rate limit: wait before sending another request.')
            prompt = self.prompt.get('1.0', 'end-1c')
            identity = (config['endpoint'], config['model'], self.mode.get(), config['protocol'])
            contexts = [attachment_context(self.attachments)] if self.attachments else []
            if self.attach.get():
                path = self.api.get_path()
                contexts.append('--- Current tab: ' + (path.name if path else 'Untitled') + ' ---\n' + self.api.get_text())
            context = '\n\n'.join(contexts)
            messages = build_messages(config, self.mode.get(), prompt, context, self.histories.get(identity, []))
            key = self.checked_key(config)
        except ValueError as error:
            messagebox.showerror('AI Assistant', str(error), parent=self.window)
            return
        self.pending_prompt = prompt
        self.config, self.busy = config, True
        self.used += 1
        self.send.configure(state='disabled'); self.models_button.configure(state='disabled'); self.status.configure(text=f'Waiting · request {self.used}/{config["session_requests"]}')
        names = [item['path'].name for item in self.attachments]
        if self.attach.get():
            names.append('current tab')
        self.append('You:\n' + prompt + ('\n[Attached: ' + ', '.join(names) + ']' if names else '') + '\n\n')
        self.prompt.delete('1.0', 'end')
        threading.Thread(target=self.worker, args=(config, key, messages, identity), daemon=True).start()

    def worker(self, config, key, messages, identity):
        try:
            if messages is None:
                event = dict(ok=True, models=list_models(config, key), identity=identity)
            else:
                reply, usage = send_request(config, key, messages)
                event = dict(ok=True, reply=reply, usage=usage, identity=identity, messages=messages)
        except HTTPError as error:
            status = error.code
            delay = 0
            if status == 429:
                try:
                    delay = max(1, min(86400, int(error.headers.get('Retry-After', '30'))))
                except (ValueError, TypeError, AttributeError):
                    delay = 30
            message = {400: 'Provider rejected request parameters. Check API format, model ID, token cap, and reasoning support.', 404: 'Model or endpoint not found. Refresh models and check the API URL.', 401: 'API key rejected.', 403: 'Provider denied access to this model.',
                       402: 'Provider credits or quota exhausted.', 429: 'Provider rate or quota limit; check usage/credits and wait before retrying.'}.get(status, f'Provider returned HTTP {status}.')
            event = dict(ok=False, message=message, pause=delay)
        except (URLError, TimeoutError, ConnectionError):
            event = dict(ok=False, message='Could not reach this provider. Check internet access, proxy/firewall, and endpoint. For Ollama, start the local server and pull a model first.', pause=0)
        except AIResponseError as error:
            event = dict(ok=False, message=str(error), pause=0)
        except Exception:
            # Worker boundary: return a sanitized result even for malformed provider responses.
            event = dict(ok=False, message='Request failed or reply was not compatible. Check endpoint, model, connectivity, and reply limit.', pause=0)
        if not self.stopped.is_set():
            self.results.put_nowait(event)

    def poll(self):
        self.poll_job = None
        try:
            event = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if not event['ok'] and self.pending_prompt and not self.prompt.get('1.0', 'end-1c').strip():
                self.prompt.insert('1.0', self.pending_prompt)
            self.pending_prompt = None
            self.send.configure(state='normal'); self.models_button.configure(state='normal')
            if event['ok'] and 'models' in event:
                current = (self.values['endpoint'].get().strip(), self.values['protocol'].get())
                if current == event['identity']:
                    self.model_picker.configure(values=event['models'])
                    self.status.configure(text=f'{len(event["models"])} models listed · choose a text model compatible with the selected API format')
                else:
                    self.status.configure(text='Provider settings changed; refresh models again.')
            elif event['ok']:
                self.last_reply = event['reply']
                self.histories[event['identity']] = [*event['messages'][1:], {'role': 'assistant', 'content': event['reply']}][-4:]
                self.append('Assistant:\n' + event['reply'] + '\n\n')
                tokens = event['usage'].get('total_tokens', 'not reported')
                self.status.configure(text=f'Ready · {self.used} requests · last tokens: {tokens}')
                if event['usage'].get('incomplete'):
                    self.append('Notice: the provider returned an incomplete reply. Review it before use; check provider restrictions or increase the output cap if needed. No automatic retry was sent.\n\n')
                self.api.emit('ai_assistant.reply', text=event['reply'], model=event['identity'][1])
            else:
                self.pause_until = time.monotonic() + event['pause']
                self.status.configure(text=event['message'])
                self.append('Notice: ' + event['message'] + '\n\n')
        if not self.stopped.is_set():
            self.poll_job = self.root.after(100, self.poll)

    def reset(self):
        self.histories.clear()
        if self.window:
            self.status.configure(text='History reset · request count is unchanged')

    def open_reply(self):
        if self.last_reply:
            self.editor.new()
            self.api.replace_text(self.last_reply)

    def cleanup(self):
        self.provider_keys.clear()
        self.stopped.set()
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        if self.window is not None and self.window.winfo_exists():
            self.key.set('')
            self.window.destroy()


def register(api):
    AIAssistant(api)
