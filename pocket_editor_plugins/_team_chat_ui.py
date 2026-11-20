"""Tk team-chat panel. A single worker owns all sockets and host lifecycle."""
import json
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import platform
from urllib.parse import urlsplit
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pocket_editor_plugins._team_chat import ChatClient, ChatServer, field
from pocket_editor_plugins._team_project_sync import ProjectCopy, atomic_file
from pocket_editor_plugins._team_relay import RelayManager, platform_info, installed_cloudflared, setup_commands, setup_script
from pocket_editor_plugins._team_chat_feed import ChatFeed
from pocket_editor_plugins._team_invites import read_invitation, write_invitation
from pocket_editor_plugins._team_network import detect_public_ipv4, public_https_url, suggested_lan_ipv4, lan_host_url


def default_storage():
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'pocket-editor/team-chat'


class ChatWorker:
    def __init__(self):
        self.commands = queue.Queue(maxsize=8)
        self.events = queue.Queue(maxsize=200)
        self.stopped = threading.Event()
        self.protected_paths = frozenset()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def emit(self, kind, value=None):
        while not self.stopped.is_set():
            try:
                self.events.put((kind, value), timeout=.1)
                return
            except queue.Full:
                pass

    def submit(self, kind, value=None):
        self.commands.put_nowait((kind, value))

    def close(self):
        self.stopped.set()

    def run(self):
        client = server = copy = None
        cursor = 0
        failed = False
        try:
            while not self.stopped.is_set():
                try:
                    kind, value = self.commands.get(timeout=.8)
                except queue.Empty:
                    kind, value = 'poll', None
                try:
                    if kind == 'local_network':
                        self.emit('local_network', suggested_lan_ipv4())
                    elif kind in ('join', 'host'):
                        if server:
                            server.close()
                            server = None
                        client = None
                        cursor = 0
                        address, token, room, author, bind, port, storage, project = value
                        # Validate all client fields before starting a host.
                        field(room, 'room', 100)
                        field(author, 'name', 80)
                        if kind == 'host':
                            server = ChatServer(storage, token, bind, int(port), project or None, room,
                                                lambda name: name not in self.protected_paths)
                            listen_ip = server.httpd.server_address[0]
                            control_ip = '127.0.0.1' if listen_ip == '0.0.0.0' else listen_ip
                            address = f'http://{control_ip}:{server.port}'
                        candidate = ChatClient(address, token, room, author)
                        rows = candidate.messages()
                        session = candidate.json_request('/session')
                        copy = ProjectCopy(candidate, project, storage) if project else None
                        if copy:
                            self.emit('progress', 'Copying the shared project…')
                            result = copy.pull()
                            self.emit('synced', ('pull', result))
                        client = candidate
                        self.emit('connected', {'address': address, 'host': kind == 'host', 'admin': session['admin'], 'session': session, 'local_ip': suggested_lan_ipv4()})
                        if rows:
                            cursor = rows[-1]['id']
                            self.emit('messages', rows)
                        failed = False
                    elif kind == 'leave':
                        client = copy = None
                        if server:
                            server.close()
                            server = None
                        self.emit('left')
                    elif client:
                        if kind == 'network_info':
                            self.emit('connection_info', client.json_request('/session'))
                        elif kind in ('pull_project', 'publish_project'):
                            if copy is None:
                                raise ValueError('Leave, choose a local project folder, and join again to copy the project.')
                            result = copy.pull() if kind == 'pull_project' else copy.publish()
                            self.emit('synced', ('pull' if kind == 'pull_project' else 'publish', result))
                        elif kind == 'invite':
                            self.emit('invited', client.json_request('/clients', data=json.dumps(value if isinstance(value, dict) else {'name': value}).encode()))
                        elif kind == 'clients':
                            self.emit('clients', client.json_request('/clients')['clients'])
                        elif kind == 'delete_client':
                            client.json_request('/clients', data=json.dumps({'delete': value}).encode())
                            self.emit('clients', client.json_request('/clients')['clients'])
                            self.emit('client_deleted', value)
                        elif kind == 'revoke':
                            client.json_request('/clients', data=json.dumps({'revoke': value}).encode())
                            self.emit('clients', client.json_request('/clients')['clients'])
                        elif kind == 'send':
                            client.send(value)
                            self.emit('sent', value)
                        elif kind == 'upload':
                            client.upload(value)
                            self.emit('uploaded', Path(value).name)
                        elif kind == 'download':
                            item, destination = value
                            client.download(item, destination)
                            self.emit('downloaded', str(destination))
                        rows = client.messages(cursor)
                        if rows:
                            cursor = rows[-1]['id']
                            self.emit('messages', rows)
                        if failed:
                            self.emit('recovered')
                        failed = False
                except Exception as error:
                    if kind in ('join', 'host'):
                        client = None
                        if server:
                            server.close()
                            server = None
                    if kind != 'poll' or not failed:
                        self.emit('error', (kind, str(error)))
                    failed = True
        finally:
            if server:
                server.close()


class ChatPane(ttk.Frame):
    def __init__(self, parent, root, storage=None, editor=None):
        super().__init__(parent, padding=12)
        self.root, self.editor = root, editor
        self.storage = Path(storage) if storage else default_storage()
        self.worker = ChatWorker()
        self.relay = RelayManager(self.worker.emit)
        self.relay_window = None
        self.relay_url = tk.StringVar()
        self.relay_status = tk.StringVar(value='Not sharing over the internet.')
        self.relay_active = False
        self.closed = self.connected = self.hosting = self.admin = self.connecting = self.transferring = False
        self.attachments = {}
        self.last_sync = ''
        self.key_window = None
        self.client_rows = {}
        self.last_invite = None
        self.public_ip_pending = False
        self.public_ip_generation = 0
        self.settings_visible = True
        self.contributors = set()
        self.project = tk.StringVar()
        self.address = tk.StringVar(value='http://127.0.0.1:8765')
        self.room = tk.StringVar(value='project')
        self.author = tk.StringVar(value=os.environ.get('USER', os.environ.get('USERNAME', 'Teammate')))
        self.token = tk.StringVar()
        self.bind, self.port = tk.StringVar(value='0.0.0.0'), tk.StringVar(value='8765')
        self.role = tk.StringVar(value='Client')
        self.selected_role = 'Client'
        self.local_ip = None
        self.session_info = {}
        self.suggested_address = None
        self.network_window = None
        self.network_public_pending = False
        self.public_lookup_pending = False
        self.public_ip_value = tk.StringVar()
        self.public_lookup_status = tk.StringVar(value="Click Detect My Public IP. No hosting or joining is required.")
        self.connection_summary = tk.StringVar(value='Client: load an invitation; no client listening port is needed.')
        self.status = tk.StringVar(value='Load a client invitation or start your own team server.')
        toolbar = ttk.Frame(self)
        toolbar.pack(fill='x', pady=(0, 8))
        ttk.Label(toolbar, text='Team Space', style='Heading.TLabel').pack(side='left')
        self.leave_button = ttk.Button(toolbar, text='Leave / Stop Host', command=self.leave)
        self.leave_button.pack(side='right')
        self.connection_button = ttk.Button(toolbar, text='Setup', command=self.toggle_settings)
        self.connection_button.pack(side='right', padx=6)
        self.details_button = ttk.Button(toolbar, text='Details', command=self.show_network)
        self.details_button.pack(side='right', padx=(0, 6))
        self.load_invite_button = ttk.Button(toolbar, text='Load Invitation…', command=self.load_invitation)
        self.load_invite_button.pack(side='right')
        self.public_ip_bar = ttk.LabelFrame(self, text='Your Public IP — Host & Client', padding=(10, 6))
        self.public_ip_bar.pack(fill='x', pady=(0, 8))
        ip_row = ttk.Frame(self.public_ip_bar)
        ip_row.pack(fill='x')
        self.main_detect_ip_button = ttk.Button(ip_row, text='Detect My Public IP', command=self.start_public_ip_lookup)
        self.main_detect_ip_button.pack(side='left')
        ttk.Entry(ip_row, textvariable=self.public_ip_value, state='readonly', width=19).pack(side='left', padx=8)
        self.copy_ip_button = ttk.Button(ip_row, text='Copy IP', command=self.copy_public_ip, state='disabled')
        self.copy_ip_button.pack(side='left')
        ttk.Label(self.public_ip_bar, textvariable=self.public_lookup_status, wraplength=690,
                  style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
        self.main_relay_bar = ttk.LabelFrame(self, text='Internet Sharing', padding=(10, 6))
        relay_actions = ttk.Frame(self.main_relay_bar)
        relay_actions.pack(fill='x')
        self.install_relay_button = ttk.Button(relay_actions, text='Install Relay', command=lambda: self.start_relay(True))
        self.install_relay_button.pack(side='left')
        self.start_sharing_button = ttk.Button(relay_actions, text='Start Internet Sharing', command=self.start_relay)
        self.start_sharing_button.pack(side='left', padx=6)
        self.stop_sharing_button = ttk.Button(relay_actions, text='Stop Sharing', command=self.relay.stop)
        self.stop_sharing_button.pack(side='left')
        ttk.Label(self.main_relay_bar, textvariable=self.relay_status, wraplength=690,
                  style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
        ttk.Entry(self.main_relay_bar, textvariable=self.relay_url, state='readonly').pack(fill='x', pady=(4, 0))
        self.relay_control_state = None
        self.settings_box = ttk.LabelFrame(self, text='Choose Host or Client', padding=10)
        self.settings_box.pack(fill='x', pady=(0, 8))
        role_row = ttk.Frame(self.settings_box)
        role_row.pack(fill='x', pady=(0, 8))
        self.role_buttons = []
        for mode, label in (('Host', 'Host a project'), ('Client', 'Join as client')):
            button = ttk.Radiobutton(role_row, text=label, variable=self.role, value=mode, command=self.apply_role)
            button.pack(side='left', padx=(0, 16))
            self.role_buttons.append(button)
        grid = ttk.Frame(self.settings_box)
        self.connection_grid = grid
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)
        self.entries = []
        for row, (label, variable) in enumerate((('Server address', self.address), ('Group / room', self.room),
                                                ('Your name', self.author), ('Join key', self.token))):
            label_widget = ttk.Label(grid, text=label)
            label_widget.grid(row=row, column=0, sticky='w', padx=(0, 10), pady=3)
            if row == 0:
                self.server_label = label_widget
            elif row == 3:
                self.key_label = label_widget
            entry = ttk.Entry(grid, textvariable=variable, show='•' if variable is self.token else '')
            entry.grid(row=row, column=1, sticky='ew', pady=3)
            self.entries.append(entry)
            if variable is self.address:
                self.server_entry = entry
            if variable is self.token:
                self.token_entry = entry
        self.reveal = tk.BooleanVar()
        self.reveal_button = ttk.Checkbutton(grid, text='Show', variable=self.reveal,
                        command=lambda: self.token_entry.configure(show='' if self.reveal.get() else '•'))
        self.reveal_button.grid(row=3, column=2, padx=(6, 0))
        ttk.Label(grid, text='Local project folder').grid(row=4, column=0, sticky='w', padx=(0, 10), pady=3)
        project_entry = ttk.Entry(grid, textvariable=self.project)
        project_entry.grid(row=4, column=1, sticky='ew', pady=3)
        self.entries.append(project_entry)
        self.project_browse = ttk.Button(grid, text='Choose…', command=self.choose_project)
        self.project_browse.grid(row=4, column=2, padx=(6, 0))
        self.host_options = ttk.Frame(self.settings_box)
        host_row = self.host_options
        host_row.pack(fill='x', pady=(8, 0))
        ttk.Label(host_row, text='Listen IP').pack(side='left')
        bind_entry = ttk.Entry(host_row, textvariable=self.bind, width=13)
        bind_entry.pack(side='left', padx=6)
        ttk.Label(host_row, text='Host port').pack(side='left')
        port_entry = ttk.Entry(host_row, textvariable=self.port, width=6)
        port_entry.pack(side='left', padx=6)
        self.entries.extend((bind_entry, port_entry))
        self.connection_hint = ttk.Label(self.settings_box, wraplength=680, style='Muted.TLabel')
        self.connection_hint.pack(anchor='w', pady=(6, 0))
        self.connection_actions = ttk.Frame(self.settings_box)
        self.connection_actions.pack(fill='x', pady=(8, 0))
        self.host_button = ttk.Button(self.connection_actions, text='Start Host', command=lambda: self.connect(True))
        self.join_button = ttk.Button(self.connection_actions, text='Join Team', command=self.connect)
        self.relay_button = ttk.Button(self.connection_actions, text='Easy Internet Setup…', command=self.show_relay)
        ttk.Label(self, textvariable=self.connection_summary, wraplength=720, style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        ttk.Label(self, textvariable=self.status, wraplength=720).pack(anchor='w', pady=(0, 8))
        self.pages = ttk.Notebook(self)
        self.pages.pack(fill='both', expand=True)
        chat_tab = tk.Frame(self.pages, background='#111b21')
        self.files_tab = ttk.Frame(self.pages, padding=10)
        copy_tab = ttk.Frame(self.pages, padding=12)
        self.pages.add(chat_tab, text='Group Chat')
        self.pages.add(self.files_tab, text='Shared Files')
        self.pages.add(copy_tab, text='Project Copies')
        chat_tab.columnconfigure(0, weight=1)
        chat_tab.rowconfigure(1, weight=1)
        header = tk.Frame(chat_tab, background='#202c33', padx=14, pady=10)
        header.grid(row=0, column=0, sticky='ew')
        avatar = tk.Canvas(header, width=40, height=40, background='#202c33', highlightthickness=0)
        avatar.create_oval(1, 1, 39, 39, fill='#00a884', outline='')
        avatar.create_text(20, 20, text='T', fill='white', font=('TkDefaultFont', 16, 'bold'))
        avatar.pack(side='left', padx=(0, 12))
        titles = tk.Frame(header, background='#202c33')
        titles.pack(side='left', fill='x', expand=True)
        self.group_title = tk.StringVar(value='Project group')
        self.group_detail = tk.StringVar(value='Join your team to start the conversation')
        tk.Label(titles, textvariable=self.group_title, background='#202c33', foreground='#e9edef',
                 font=('TkDefaultFont', 12, 'bold'), anchor='w').pack(fill='x')
        tk.Label(titles, textvariable=self.group_detail, background='#202c33', foreground='#aebac1',
                 font=('TkDefaultFont', 9), anchor='w').pack(fill='x')
        self.history = ChatFeed(chat_tab, self.download_item)
        self.history.grid(row=1, column=0, sticky='nsew')
        composer_bar = tk.Frame(chat_tab, background='#202c33', padx=10, pady=10)
        composer_bar.grid(row=2, column=0, sticky='ew')
        composer_bar.columnconfigure(1, weight=1)
        self.attach_button = ttk.Button(composer_bar, text='＋ File', command=self.attach, width=8)
        self.attach_button.grid(row=0, column=0, padx=(0, 8))
        self.composer = tk.Text(composer_bar, height=2, wrap='word', background='#2a3942', foreground='#e9edef',
                                insertbackground='#e9edef', relief='flat', padx=10, pady=8,
                                font=('TkDefaultFont', 11), highlightthickness=0, undo=True)
        self.composer.grid(row=0, column=1, sticky='ew')
        self.composer.bind('<Return>', self.send)
        self.composer.bind('<Shift-Return>', lambda _event: None)
        self.composer.bind('<Control-Return>', self.send)
        self.send_button = tk.Button(composer_bar, text='Send ➤', command=self.send, background='#00a884',
                                     foreground='white', activebackground='#06cf9c', activeforeground='#0b141a',
                                     relief='flat', borderwidth=0, highlightthickness=0, padx=14, pady=12, cursor='hand2')
        self.send_button.grid(row=0, column=2, padx=(8, 0))
        tk.Label(chat_tab, text='Enter to send · Shift+Enter for a new line · Files up to 50 MiB',
                 background='#111b21', foreground='#8696a0', font=('TkDefaultFont', 8), pady=4).grid(row=3, column=0, sticky='ew')
        ttk.Label(self.files_tab, text='Audio, video, and files shared with the group', style='Heading.TLabel').pack(anchor='w', pady=(0, 10))
        self.files = ttk.Treeview(self.files_tab, columns=('name', 'size', 'author'), show='headings', height=4, selectmode='browse')
        for key, label, width in (('name', 'Attachment', 350), ('size', 'Size', 85), ('author', 'Shared by', 130)):
            self.files.heading(key, text=label)
            self.files.column(key, width=width)
        self.files.pack(fill='both', expand=True)
        self.download_button = ttk.Button(self.files_tab, text='Save Selected Attachment…', command=self.download)
        self.download_button.pack(anchor='e', pady=(8, 0))
        ttk.Label(copy_tab, text='Shared Project', style='Heading.TLabel').pack(anchor='w', pady=(0, 8))
        self.project_info = tk.StringVar(value='Choose a local folder before joining. The first project copy downloads automatically.')
        ttk.Label(copy_tab, textvariable=self.project_info, wraplength=650).pack(anchor='w', pady=(0, 12))
        self.pull_button = ttk.Button(copy_tab, text='Pull Project', command=lambda: self.sync_project(False))
        self.pull_button.pack(anchor='w', pady=4)
        self.publish_button = ttk.Button(copy_tab, text='Send Changes to Server', command=lambda: self.sync_project(True))
        self.publish_button.pack(anchor='w', pady=4)
        self.keys_button = ttk.Button(copy_tab, text='Client Keys & Invitations…', command=self.manage_keys)
        self.keys_button.pack(anchor='w', pady=4)
        self.internet_setup_button = ttk.Button(copy_tab, text='Easy Internet Setup…', command=self.show_relay)
        self.internet_setup_button.pack(anchor='w', pady=4)
        ttk.Label(copy_tab, text='Close project tabs before syncing. Conflicting edits stay local and receive a separate recovery backup.\n'
                  'Your invitation controls access and allowed folders. The host manages deletions.',
                  wraplength=650, style='Muted.TLabel').pack(anchor='w', pady=(14, 0))
        self.apply_role()
        self.update_controls()
        self.job = root.after(80, self.poll)
        self.enqueue('local_network')

    def update_controls(self):
        self.update_relay_controls()
        locked = self.connected or self.connecting
        for button in self.role_buttons:
            button.configure(state='disabled' if locked else 'normal')
        for entry in self.entries:
            entry.configure(state='disabled' if locked else 'normal')
        self.project_browse.configure(state='disabled' if locked else 'normal')
        self.load_invite_button.configure(state='disabled' if locked else 'normal')
        for button in (self.host_button, self.join_button):
            button.configure(state='disabled' if locked else 'normal')
        self.leave_button.configure(state='normal' if self.connected else 'disabled')
        self.keys_button.configure(state='normal' if self.connected and self.admin and not self.transferring else 'disabled')
        self.internet_setup_button.configure(state='normal' if self.role.get() == 'Host' else 'disabled')
        for button in (self.send_button, self.attach_button, self.download_button, self.pull_button, self.publish_button):
            button.configure(state='normal' if self.connected and not self.transferring else 'disabled')
        if self.session_info.get('role') == 'read-only':
            self.attach_button.configure(state='disabled');self.publish_button.configure(state='disabled')

    def update_relay_controls(self):
        state = (self.role.get(), self.connected, self.hosting, self.connecting, self.relay.running)
        if state == self.relay_control_state:
            return
        self.relay_control_state = state
        host_mode, connected, hosting, connecting, running = state
        self.install_relay_button.configure(state='normal' if host_mode == 'Host' and not running and not connecting else 'disabled')
        self.start_sharing_button.configure(state='normal' if connected and hosting and not running else 'disabled')
        self.stop_sharing_button.configure(state='normal' if running else 'disabled')

    def select_role(self, mode):
        self.role.set(mode)
        self.apply_role()

    def apply_role(self):
        mode = self.role.get()
        if mode != self.selected_role and not self.connected and not self.connecting:
            self.token.set('')
        self.selected_role = mode
        self.host_button.pack_forget()
        self.join_button.pack_forget()
        self.relay_button.pack_forget()
        if mode == 'Host':
            self.main_relay_bar.pack(fill='x', pady=(0, 8), after=self.public_ip_bar)
            if self.settings_visible:
                self.settings_box.pack_configure(after=self.main_relay_bar)
            self.server_label.grid_remove()
            self.server_entry.grid_remove()
            self.key_label.grid_remove()
            self.token_entry.grid_remove()
            self.reveal_button.grid_remove()
            self.host_options.pack(fill='x', pady=(8, 0), before=self.connection_hint)
            self.host_button.pack(side='right')
            self.relay_button.pack(side='left')
            self.connection_hint.configure(text='Choose the shared folder and start. LAN listening uses 0.0.0.0; the actual host IP and port appear after starting.')
        else:
            self.main_relay_bar.pack_forget()
            if self.settings_visible:
                self.settings_box.pack_configure(after=self.public_ip_bar)
            for widget in (self.server_label, self.server_entry, self.key_label, self.token_entry, self.reveal_button):
                widget.grid()
            self.host_options.pack_forget()
            self.join_button.pack(side='right')
            self.connection_hint.configure(text='Load an invitation, choose a local folder, and Join. Your outgoing client port is automatic; no client listening port is needed.')

        if not self.connected and not self.connecting:
            if mode == 'Host':
                self.connection_summary.set('Host not running · configured port ' + self.port.get() + ' · local IP ' + (self.local_ip or 'detecting…'))
            else:
                self.connection_summary.set('Client: load an invitation; your outgoing port is automatic.')
        self.render_network()
        self.update_controls()

    def show_network(self):
        if self.network_window is not None and self.network_window.winfo_exists():
            self.network_window.lift()
            self.render_network()
            return
        self.network_window = tk.Toplevel(self)
        self.network_window.title('Team Connection Details')
        self.network_window.geometry('690x410')
        body = ttk.Frame(self.network_window, padding=16)
        body.pack(fill='both', expand=True)
        self.network_text = tk.Text(body, wrap='word', height=12, state='disabled')
        self.network_text.pack(fill='both', expand=True)
        self.network_public_ip = tk.StringVar(value=self.public_ip_value.get() or self.public_lookup_status.get())
        ttk.Label(body, textvariable=self.network_public_ip, wraplength=650).pack(anchor='w', pady=8)
        actions = ttk.Frame(body)
        actions.pack(fill='x')
        self.network_detect_button = ttk.Button(actions, text='Detect My Public IP', command=self.detect_network_public_ip)
        self.network_detect_button.pack(side='left')
        ttk.Button(actions, text='Refresh Connection', command=self.refresh_network).pack(side='right')
        self.render_network()
        self.update_public_ip_controls()
        self.refresh_network()

    def refresh_network(self):
        if self.connected:
            self.enqueue('network_info')
        else:
            self.enqueue('local_network')

    def render_network(self):
        if self.network_window is None or not self.network_window.winfo_exists():
            return
        session = self.session_info
        lines = ['Role: ' + self.role.get(), 'Suggested local network IP: ' + (self.local_ip or 'Not detected')]
        if self.hosting or self.role.get() == 'Host':
            lines += [f"Host listening: {session.get('listen_ip', self.bind.get())}:{session.get('listen_port', self.port.get())}",
                      'Suggested LAN invitation URL: ' + (self.suggested_address or 'Enter your reachable address manually'),
                      'Public HTTPS invitation URL is configured in Client Keys & Invitations.']
        else:
            parsed = urlsplit(self.address.get())
            try:
                host_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            except ValueError:
                host_port = 'Invalid port'
            lines += ['Host address from invitation: ' + self.address.get(), 'Host connection port: ' + str(host_port)]
            if session.get('listen_port'):
                lines += [f"Host service listener (reported): {session.get('listen_ip', '')}:{session['listen_port']}"]
            if session.get('peer_ip'):
                lines += [f"Your connection check, as seen by host: {session['peer_ip']}:{session['peer_port']}"]
            lines += ['Your source port is chosen automatically and changes between requests.', 'You do not need a client listening port or incoming port-forwarding rule.']
        lines += ['', 'A reverse proxy can appear as the peer address. Public IP detection does not test inbound connectivity.']
        self.network_text.configure(state='normal')
        self.network_text.delete('1.0', 'end')
        self.network_text.insert('1.0', '\n'.join(lines))
        self.network_text.configure(state='disabled')

    def update_public_ip_controls(self):
        for name in ('main_detect_ip_button', 'public_ip_button', 'network_detect_button'):
            button = getattr(self, name, None)
            if button is not None and button.winfo_exists():
                button.configure(state='disabled' if self.public_lookup_pending else 'normal')
        self.main_detect_ip_button.configure(text='Detecting…' if self.public_lookup_pending else 'Detect My Public IP')
        available = bool(self.public_ip_value.get()) and not self.public_lookup_pending
        self.copy_ip_button.configure(state='normal' if available else 'disabled')
        button = getattr(self, 'use_public_ip_button', None)
        if button is not None and button.winfo_exists():
            button.configure(state='normal' if available else 'disabled')

    def start_public_ip_lookup(self):
        if self.public_lookup_pending or self.closed:
            return
        self.public_lookup_pending = self.public_ip_pending = self.network_public_pending = True
        self.public_ip_value.set('')
        self.public_lookup_status.set('Detecting public IPv4 using HTTPS services…')
        self.update_public_ip_controls()
        worker = self.worker
        def lookup():
            try:
                address, error = detect_public_ipv4(), None
            except Exception as failure:
                address, error = None, str(failure)
            worker.emit('detected_public_ip', (address, error))
        threading.Thread(target=lookup, daemon=True).start()

    def detect_network_public_ip(self):
        self.start_public_ip_lookup()

    def copy_public_ip(self):
        if self.public_ip_value.get():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.public_ip_value.get())

    def relay_port(self):
        value = self.session_info.get('listen_port', self.port.get())
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError('Start the host first to obtain its listening port.')
        return port

    def show_relay(self):
        if self.relay_window is not None and self.relay_window.winfo_exists():
            self.relay_window.lift()
            return
        self.relay_window = tk.Toplevel(self)
        self.relay_window.title('Easy Internet Sharing')
        self.relay_window.geometry('760x560')
        self.relay_window.minsize(650, 480)
        body = ttk.Frame(self.relay_window, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Host → HTTPS relay → invited clients', style='Heading.TLabel').pack(anchor='w')
        try:
            detected = platform_info()['display']
        except ValueError as error:
            detected = str(error)
        ttk.Label(body, text='Detected OS: ' + detected, wraplength=700).pack(anchor='w', pady=(8, 4))
        ttk.Label(body, text='Shared project: ' + (self.project.get() or 'Chat only (no project folder)'), wraplength=700).pack(anchor='w', pady=(0, 6))
        ttk.Label(body, text='Cloudflared: ' + (installed_cloudflared() or 'Not installed — setup downloads the official verified binary.'),
                  wraplength=700, style='Muted.TLabel').pack(anchor='w')
        ttk.Label(body, text='1. Select the shared project and Start Host.\n'
                  '2. Start Internet Sharing below. Missing tools install in your user folder.\n'
                  '3. Export a client invitation. Its address is filled with the verified HTTPS URL.',
                  wraplength=700).pack(anchor='w', pady=12)
        ttk.Label(body, text='Cloudflare Quick Tunnel carries the traffic; your host keeps the project.\n'
                  'The temporary URL changes after restart. No account, domain, or router port forwarding is required.\n'
                  'Only invited clients with a valid application key can use the project. Keep the host and tunnel running.',
                  wraplength=700, style='Muted.TLabel').pack(anchor='w', pady=(0, 10))
        ttk.Label(body, textvariable=self.relay_status, wraplength=700).pack(anchor='w', pady=(0, 8))
        ttk.Entry(body, textvariable=self.relay_url, state='readonly').pack(fill='x')
        controls = ttk.Frame(body)
        controls.pack(fill='x', pady=10)
        ttk.Button(controls, text='Install / Check Tool', command=lambda: self.start_relay(True)).pack(side='left')
        ttk.Button(controls, text='Start Internet Sharing', command=self.start_relay).pack(side='left', padx=6)
        ttk.Button(controls, text='Stop Sharing', command=self.relay.stop).pack(side='left')
        ttk.Label(body, text='Or run these commands on this same computer:', style='Muted.TLabel').pack(anchor='w')
        commands = tk.Text(body, height=4, wrap='word')
        commands.pack(fill='both', expand=True, pady=(4, 8))
        try:
            script = Path(__file__).resolve().parents[1] / 'tools/team_relay.py'
            commands.insert('1.0', '\n'.join(setup_commands(script, self.relay_port())))
        except ValueError as error:
            commands.insert('1.0', str(error))
        commands.configure(state='disabled')
        ttk.Button(body, text='Save Setup Script…', command=self.save_relay_script).pack(anchor='e')

    def save_relay_script(self):
        try:
            script = Path(__file__).resolve().parents[1] / 'tools/team_relay.py'
            content = setup_script(script, self.relay_port())
            extension = '.ps1' if platform.system() == 'Windows' else '.sh'
            filename = filedialog.asksaveasfilename(parent=self.relay_window, title='Save Relay Setup Script',
                        initialfile='setup-team-relay' + extension, defaultextension=extension)
            if filename:
                atomic_file(Path(filename), content.encode())
                if platform.system() != 'Windows':
                    Path(filename).chmod(0o700)
        except (OSError, ValueError) as error:
            messagebox.showerror('Relay Setup', str(error), parent=self.relay_window)

    def start_relay(self, setup_only=False):
        if not setup_only and (not self.connected or not self.hosting):
            self.relay_status.set('Choose Host, select the project folder, and Start Host before sharing.')
            return
        if not setup_only and self.session_info.get('listen_ip') not in ('0.0.0.0', '127.0.0.1'):
            self.relay_status.set('For this relay, restart the host with Listen IP 127.0.0.1 or 0.0.0.0.')
            return
        try:
            credentials = None if setup_only else (self.token.get(), self.room.get(), self.author.get())
            self.relay.start(self.relay_port(), credentials, setup_only)
            self.relay_status.set('Preparing the relay tool…')
        except (OSError, RuntimeError, ValueError) as error:
            self.relay_status.set(str(error))

    def show_settings(self, visible):
        self.settings_visible = visible
        if visible:
            self.settings_box.pack(fill='x', pady=(0, 8), after=self.main_relay_bar if self.role.get() == 'Host' else self.public_ip_bar)
        else:
            self.settings_box.pack_forget()

    def toggle_settings(self):
        self.show_settings(not self.settings_visible)

    def load_invitation(self):
        if self.connected or self.connecting:
            return
        filename = filedialog.askopenfilename(parent=self, title='Load Team Invitation',
                                              filetypes=[('Team invitation', '*.team-invite'), ('JSON', '*.json'), ('All files', '*')])
        if not filename:
            return
        try:
            invite = read_invitation(filename)
            folder = filedialog.askdirectory(parent=self, title='Choose your local copy folder for ' + invite['project_name'])
            if not folder:
                return
            self.select_role('Client')
            self.address.set(invite['server'])
            self.room.set(invite['room'])
            self.author.set(invite['client_name'])
            self.token.set(invite['client_key'])
            self.project.set(folder)
            self.show_settings(True)
            self.status.set('Invitation loaded for ' + invite['client_name'] + '. Click Join Team to connect.')
        except (OSError, ValueError, UnicodeError) as error:
            messagebox.showerror('Team Invitation', str(error), parent=self)

    def export_invitation(self):
        if not self.last_invite or not self.admin or not self.connected:
            return
        invite = self.last_invite
        data = {'format': 'pocket-team-invite', 'version': 1, 'server': self.invite_address.get().strip(),
                'room': invite['room'], 'client_name': invite['name'], 'client_key': invite['key'],
                'project_name': Path(self.project.get()).name if self.project.get() else self.room.get()}
        try:
            from pocket_editor_plugins._team_invites import validate_invitation
            validate_invitation(data)
            filename = filedialog.asksaveasfilename(parent=self.key_window, title='Export Invitation for ' + invite['name'],
                        defaultextension='.team-invite', filetypes=[('Team invitation', '*.team-invite')])
            if filename:
                write_invitation(filename, data)
                self.status.set('Invitation saved for ' + invite['name'] + '. Send that file to the client.')
        except (OSError, ValueError) as error:
            messagebox.showerror('Team Invitation', str(error), parent=self.key_window)

    def choose_project(self):
        folder = filedialog.askdirectory(parent=self, title='Host shared project / Client local copy folder')
        if folder:
            self.project.set(folder)

    def tabs_closed(self):
        if self.editor is None or not self.project.get():
            return True
        folder = Path(self.project.get()).expanduser().resolve()
        self.editor.remember_document()
        for document in self.editor.documents:
            if document['path'] and document['path'].resolve().is_relative_to(folder):
                self.status.set('Close this project’s file tabs before copying or sending project files.')
                return False
        return True

    def sync_project(self, publish):
        if not self.connected or self.transferring or not self.tabs_closed():
            return
        if self.enqueue('publish_project' if publish else 'pull_project'):
            self.transferring = True
            self.status.set('Sending local changes…' if publish else 'Pulling the shared project…')
            self.update_controls()

    def manage_keys(self):
        if not self.connected or not self.admin:
            return
        if self.key_window is not None and self.key_window.winfo_exists():
            self.key_window.lift()
            return
        self.key_window = tk.Toplevel(self)
        self.key_window.title('Server — Client Join Keys')
        self.key_window.geometry('730x650')
        self.key_window.minsize(680, 600)
        body = ttk.Frame(self.key_window, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Issue one key per client. Keep the host key private.', wraplength=580).pack(anchor='w')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=10)
        self.client_name = tk.StringVar()
        ttk.Entry(row, textvariable=self.client_name).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Generate Client Key', command=self.invite).pack(side='left', padx=(8, 0))
        access = ttk.Frame(body);access.pack(fill='x', pady=(0, 8))
        ttk.Label(access, text='Access').pack(side='left')
        self.client_access = tk.StringVar(value='member')
        ttk.Combobox(access, textvariable=self.client_access, values=('read-only', 'member', 'admin'), state='readonly', width=12).pack(side='left', padx=8)
        self.client_folders = tk.StringVar()
        ttk.Label(body, text='Allowed project folders (comma separated; blank means all)').pack(anchor='w')
        ttk.Entry(body, textvariable=self.client_folders).pack(fill='x', pady=(0, 8))
        ttk.Label(body, text='Client-facing server URL (LAN/VPN IP or HTTPS address)').pack(anchor='w')
        self.invite_address = tk.StringVar(value=self.suggested_address or ('' if '127.0.0.1' in self.address.get() or 'localhost' in self.address.get() else self.address.get()))
        ttk.Entry(body, textvariable=self.invite_address).pack(fill='x', pady=(3, 8))
        ip_row = ttk.Frame(body)
        ip_row.pack(fill='x', pady=(0, 5))
        self.public_ip = self.public_ip_value
        self.public_ip_status = self.public_lookup_status
        self.public_https_port = tk.StringVar(value='443')
        self.public_ip_button = ttk.Button(ip_row, text='Detect Public IP', command=self.discover_public_ip)
        self.public_ip_button.pack(side='left')
        self.public_ip_button.configure(state='disabled' if self.public_ip_pending else 'normal')
        ttk.Entry(ip_row, textvariable=self.public_ip, state='readonly', width=17).pack(side='left', padx=6)
        ttk.Label(ip_row, text='HTTPS port').pack(side='left')
        ttk.Entry(ip_row, textvariable=self.public_https_port, width=6).pack(side='left', padx=6)
        self.use_public_ip_button = ttk.Button(ip_row, text='Use HTTPS IP', command=self.use_public_ip, state='disabled')
        self.use_public_ip_button.pack(side='left')
        ttk.Label(body, textvariable=self.public_ip_status, wraplength=650, style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        ttk.Label(body, text='Detection does not open router/firewall ports or verify reachability.\n'
                  'Public invitations need HTTPS configured for this address, or use a private VPN address.',
                  wraplength=650, style='Muted.TLabel').pack(anchor='w', pady=(0, 10))
        self.generated_key = tk.StringVar()
        ttk.Entry(body, textvariable=self.generated_key, state='readonly').pack(fill='x')
        self.export_invite_button = ttk.Button(body, text='Export Client Invitation…', command=self.export_invitation, state='disabled')
        self.export_invite_button.pack(anchor='e', pady=(6, 4))
        ttk.Label(body, text='Generate a key, then export its invitation for that client. Only the key hash stays on the server.',
                  style='Muted.TLabel').pack(anchor='w', pady=(4, 8))
        self.client_tree = ttk.Treeview(body, columns=('name', 'state', 'ip', 'port', 'seen'), show='headings', selectmode='browse')
        self.client_tree.heading('name', text='Client')
        self.client_tree.heading('state', text='Access')
        self.client_tree.heading('ip', text='Peer IP (host sees)')
        self.client_tree.heading('port', text='Source port')
        self.client_tree.heading('seen', text='Last request')
        for column, width in (('name', 135), ('state', 70), ('ip', 155), ('port', 85), ('seen', 90)):
            self.client_tree.column(column, width=width, stretch=True)
        ttk.Label(body, text='Client source ports are automatic and change; a reverse proxy may appear as the peer IP.',
                  style='Muted.TLabel', wraplength=650).pack(anchor='w', pady=(0, 6))
        self.client_tree.pack(fill='both', expand=True)
        controls = ttk.Frame(body)
        controls.pack(side='bottom', fill='x', pady=(8, 0), before=self.client_tree)
        ttk.Button(controls, text='Refresh Clients', command=lambda: self.enqueue('clients')).pack(side='left')
        ttk.Button(controls, text='Remove Client…', command=self.delete_client).pack(side='right')
        ttk.Button(controls, text='Revoke Selected Key', command=self.revoke).pack(side='right', padx=8)
        self.update_public_ip_controls()
        self.enqueue('clients')

    def discover_public_ip(self):
        self.start_public_ip_lookup()

    def use_public_ip(self):
        try:
            address = public_https_url(self.public_ip.get(), self.public_https_port.get())
        except ValueError as error:
            self.public_ip_status.set(str(error))
            return
        self.invite_address.set(address)
        self.public_ip_status.set('HTTPS address selected. Configure and verify HTTPS before sending the invitation.')

    def invite(self):
        try:
            name = field(self.client_name.get(), 'client name', 80)
        except ValueError as error:
            self.status.set(str(error))
            return
        folders = [part.strip() for part in self.client_folders.get().split(',') if part.strip()]
        self.enqueue('invite', {'name': name, 'role': self.client_access.get(), 'folders': folders})

    def revoke(self):
        selected = self.client_tree.selection()
        if selected:
            ident = self.client_rows[selected[0]]
            if self.enqueue('revoke', ident) and self.last_invite and self.last_invite['id'] == ident:
                self.last_invite = None
                self.generated_key.set('')
                self.export_invite_button.configure(state='disabled')

    def delete_client(self):
        if not self.connected or not self.admin:
            return
        selected = self.client_tree.selection()
        if not selected:
            self.status.set('Select a client to remove.');return
        row = selected[0]
        ident = self.client_rows.get(row)
        if ident is None:return
        name = self.client_tree.item(row, 'values')[0]
        if not messagebox.askyesno('Remove Client',
                f'Remove {name} from this team and invalidate their join key?\n\n'
                'Their downloaded files will remain on their computer. A new invitation is needed to rejoin.',
                parent=self.key_window):return
        self.enqueue('delete_client', ident)

    def enqueue(self, kind, value=None):
        try:
            self.worker.submit(kind, value)
            return True
        except queue.Full:
            self.status.set('Chat is busy. Please wait for the current operation.')
            return False

    def connect(self, host=False):
        if self.connected or self.connecting:
            return
        if not self.tabs_closed():
            return
        self.role.set('Host' if host else 'Client')
        self.selected_role = self.role.get()
        self.apply_role()
        token = self.token.get().strip()
        if host and not token:
            token = secrets.token_urlsafe(32)
            self.token.set(token)
        value = (self.address.get(), token, self.room.get(), self.author.get(),
                 self.bind.get(), self.port.get(), self.storage, self.project.get().strip())
        if self.enqueue('host' if host else 'join', value):
            self.connecting = True
            self.status.set('Starting host…' if host else 'Connecting…')
            self.update_controls()

    def leave(self):
        self.relay.stop()
        if self.connected and self.enqueue('leave'):
            self.connected = False
            self.connecting = True
            self.status.set('Leaving chat…')
            self.update_controls()

    def send(self, _event=None):
        if not self.connected or self.transferring:
            return 'break'
        text = self.composer.get('1.0', 'end-1c').strip()
        try:
            field(text, 'message', 8000)
        except ValueError as error:
            self.status.set(str(error))
            return 'break'
        if self.enqueue('send', text):
            self.transferring = True
            self.status.set('Sending…')
            self.update_controls()
        return 'break'

    def attach(self):
        if not self.connected or self.transferring:
            return
        filename = filedialog.askopenfilename(parent=self, title='Share audio, video, or another file',
                    filetypes=[('All files', '*'), ('Audio', '*.mp3 *.wav *.ogg *.m4a'),
                               ('Video', '*.mp4 *.webm *.mov *.mkv')])
        if filename and self.enqueue('upload', filename):
            self.transferring = True
            self.status.set('Uploading attachment…')
            self.update_controls()

    def download(self):
        if not self.connected or self.transferring:
            return
        selection = self.files.selection()
        if not selection:
            self.status.set('Select a shared attachment first.')
            return
        item = self.attachments[selection[0]]
        self.download_item(item)

    def download_item(self, item):
        if not self.connected or self.transferring:
            return
        filename = filedialog.asksaveasfilename(parent=self, title='Save shared attachment', initialfile=item['name'])
        if filename and self.enqueue('download', (item, filename)):
            self.transferring = True
            self.status.set('Downloading attachment…')
            self.update_controls()

    def append(self, rows):
        self.history.append(rows, self.author.get())
        self.contributors.update(row['author'] for row in rows)
        self.group_detail.set(f"{'Hosting' if self.hosting else 'Connected'} · {len(self.contributors)} contributors in this conversation")
        for row in rows:
            if row.get('file_id'):
                item = self.files.insert('', 'end', values=(row['name'], f"{row['size'] / 1024:.1f} KiB", row['author']))
                self.attachments[item] = row
                if len(self.attachments) > 500:
                    oldest = next(iter(self.attachments))
                    del self.attachments[oldest]
                    self.files.delete(oldest)

    def poll(self):
        self.job = None
        if self.closed:
            return
        if self.editor is not None and self.project.get():
            folder = Path(self.project.get()).expanduser().resolve()
            self.editor.remember_document()
            protected = set()
            for document in self.editor.documents:
                path = document['path']
                if path and path.resolve().is_relative_to(folder):
                    protected.add(path.resolve().relative_to(folder).as_posix())
            self.worker.protected_paths = frozenset(protected)
        try:
            while True:
                kind, value = self.worker.events.get_nowait()
                if kind == 'connected':
                    self.connected, self.connecting = True, False
                    self.hosting = value['host']
                    self.admin = value['admin']
                    self.address.set(value['address'])
                    self.local_ip = value.get('local_ip')
                    self.session_info = value.get('session', {})
                    if self.hosting:
                        actual_port = self.session_info.get('listen_port', self.port.get())
                        self.port.set(str(actual_port))
                        self.suggested_address = lan_host_url(self.bind.get(), actual_port, self.local_ip)
                        self.connection_summary.set(f'Host · listening {self.bind.get()}:{actual_port} · ' + (self.suggested_address or 'Set a reachable invitation URL'))
                    else:
                        self.suggested_address = None
                        self.connection_summary.set('Client → ' + self.address.get() + ' · client port automatic')
                    self.render_network()
                    self.history.clear()
                    self.contributors.clear()
                    self.group_title.set(self.room.get())
                    self.group_detail.set('Hosting the group' if self.hosting else 'Connected to the group')
                    self.show_settings(False)
                    self.files.delete(*self.files.get_children())
                    self.attachments.clear()
                    self.status.set('Hosting. Create client invitations under Project Copies → Client Keys & Invitations.' if self.hosting else 'Joined the team server.')
                elif kind == 'relay':
                    state, detail = value
                    if state == 'ready':
                        if not self.connected or not self.hosting or self.relay.cancelled.is_set():
                            self.relay.stop()
                            continue
                        self.relay_active = True
                        self.relay_url.set(detail)
                        self.suggested_address = detail
                        self.relay_status.set('HTTPS relay verified. New client invitations use this URL.')
                        self.connection_summary.set('Host · Internet sharing: ' + detail)
                        if self.key_window is not None and self.key_window.winfo_exists():
                            self.invite_address.set(detail)
                    elif state == 'stopped':
                        previous = self.relay_url.get()
                        self.relay_url.set('')
                        self.relay_active = False
                        if self.suggested_address == previous:
                            self.suggested_address = lan_host_url(self.bind.get(), self.session_info.get('listen_port', self.port.get()), self.local_ip) if self.hosting else None
                        if self.key_window is not None and self.key_window.winfo_exists() and self.invite_address.get() == previous:
                            self.invite_address.set(self.suggested_address or '')
                        if not self.relay_status.get().startswith('Relay failed:'):
                            self.relay_status.set('Internet sharing stopped. Temporary invitations no longer connect.')
                        if self.hosting:
                            self.connection_summary.set('Host · ' + (self.suggested_address or 'Set a reachable invitation URL'))
                    elif state == 'error':
                        self.relay_status.set('Relay failed: ' + detail)
                    elif state == 'installed':
                        self.relay_status.set('Relay tool ready. Start the host, then Start Internet Sharing.')
                    else:
                        self.relay_status.set(detail)
                elif kind == 'local_network':
                    self.local_ip = value
                    if not self.connected and not self.connecting:
                        self.apply_role()
                    self.render_network()
                elif kind == 'connection_info':
                    self.session_info = value
                    self.render_network()
                elif kind == 'detected_public_ip':
                    self.public_lookup_pending = self.public_ip_pending = self.network_public_pending = False
                    address, error = value
                    self.public_ip_value.set(address or '')
                    self.public_lookup_status.set(error or 'Public IPv4 detected. Detection does not check incoming connectivity.')
                    if self.network_window is not None and self.network_window.winfo_exists():
                        self.network_public_ip.set(error or ('Detected outbound public IPv4: ' + address))
                    self.update_public_ip_controls()
                elif kind == 'left':
                    self.connected = self.connecting = self.hosting = self.admin = self.transferring = False
                    self.status.set('Disconnected. Your host history is kept on disk.')
                    self.session_info = {}
                    self.connection_summary.set('Disconnected · choose Host or Client to connect again.')
                    self.render_network()
                    self.group_detail.set('Disconnected')
                    self.show_settings(True)
                elif kind == 'progress':
                    self.status.set(value)
                elif kind == 'synced':
                    self.transferring = False
                    action, result = value
                    paths = result.get('copied', result.get('sent', []))
                    conflicts = result['conflicts']
                    self.last_sync = f'{len(paths)} files ' + ('copied from server.' if action == 'pull' else 'sent to server.')
                    if conflicts:
                        self.last_sync += ' Conflicts kept locally: ' + ', '.join(conflicts[:20])
                        copies = result.get('conflict_copies', {})
                        if copies:self.last_sync += ' Recovery backups: ' + str(Path(next(iter(copies.values()))).parent)
                    self.project_info.set(self.last_sync)
                    self.status.set(self.last_sync)
                    if self.editor is not None and self.project.get():
                        try:
                            self.editor.plugin_apis['project_explorer'].call_service('project_explorer.open_project', Path(self.project.get()))
                        except (KeyError, LookupError, OSError, ValueError):
                            pass
                elif kind == 'clients':
                    if self.key_window is not None and self.key_window.winfo_exists():
                        self.client_tree.delete(*self.client_tree.get_children())
                        self.client_rows.clear()
                        for row in value:
                            item = self.client_tree.insert('', 'end', values=(row['name'], 'Revoked' if row['revoked'] else 'Active', row.get('peer_ip', 'Not joined'), row.get('peer_port', '—'), time.strftime('%H:%M:%S', time.localtime(row['last_seen'])) if row.get('last_seen') else '—'))
                            self.client_rows[item] = row['id']
                elif kind == 'invited':
                    if self.key_window is not None and self.key_window.winfo_exists():
                        self.generated_key.set(value['key'])
                        self.last_invite = value
                        self.export_invite_button.configure(state='normal')
                        self.enqueue('clients')
                elif kind == 'messages':
                    self.append(value)
                elif kind == 'error':
                    action, detail = value
                    self.transferring = False
                    if action in ('join', 'host'):
                        self.connecting = self.connected = False
                    self.status.set(detail)
                elif kind in ('sent', 'uploaded', 'downloaded'):
                    self.transferring = False
                    if kind == 'sent' and self.composer.get('1.0', 'end-1c').strip() == value:
                        self.composer.delete('1.0', 'end')
                    self.status.set('Message sent.' if kind == 'sent' else
                                    ('Shared ' if kind == 'uploaded' else 'Saved ') + value)
                elif kind == 'client_deleted':
                    if self.last_invite and self.last_invite['id'] == value:
                        self.last_invite = None
                        self.generated_key.set('')
                        if self.key_window is not None and self.key_window.winfo_exists():
                            self.export_invite_button.configure(state='disabled')
                    self.status.set('Client removed. Their previous join key is no longer accepted.')
                elif kind == 'recovered':
                    self.status.set('Reconnected to team chat.')
                self.update_controls()
        except queue.Empty:
            pass
        self.update_relay_controls()
        self.job = self.root.after(80, self.poll)

    def cleanup(self):
        if self.closed:
            return
        self.closed = True
        self.relay.stop()
        if self.relay_window is not None and self.relay_window.winfo_exists():
            self.relay_window.destroy()
        self.worker.close()
        if self.network_window is not None and self.network_window.winfo_exists():
            self.network_window.destroy()
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        if self.key_window is not None and self.key_window.winfo_exists():
            self.key_window.destroy()
        # Release Tk widgets/variables on the GUI thread. Otherwise cycles can
        # be collected by a transport worker after the Tcl interpreter closes.
        if self.winfo_exists():
            self.destroy()
        self.entries.clear()
        self.role_buttons.clear()
        for name, value in list(vars(self).items()):
            if isinstance(value, (tk.Misc, tk.Variable)) and name != 'master':
                delattr(self, name)
        self.editor = None
