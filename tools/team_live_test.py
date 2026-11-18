#!/usr/bin/env python3
"""Interactive Host/Client demo with continuous checks until Stop or SIGINT."""
import argparse
import gc
import json
import os
from pathlib import Path
import queue
import secrets
import signal
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pocket_editor as core
from pocket_editor_plugins._team_chat import ChatClient
from pocket_editor_plugins._team_chat_ui import ChatPane
from pocket_editor_plugins._team_invites import write_invitation
from pocket_editor_plugins._team_project_sync import ProjectCopy, atomic_file


class LiveTest:
    def __init__(self, local_only=False, cycles=0, interval=30):
        self.base = Path(tempfile.mkdtemp(prefix='pocket-team-live-'))
        self.project = self.base / 'host-project'; self.project.mkdir()
        self.copy = self.base / 'client-project'; self.copy.mkdir()
        (self.project / 'README.txt').write_text('Temporary Team Project demo. You can edit files here.\nAutomation uses the _automation folder.\n')
        self.invitation = self.base / 'live-client.team-invite'
        self.logfile = self.base / 'test.log'
        self.statefile = self.base / 'status.json'
        self.stop_requested = threading.Event()
        self.stop_reason = ''
        self.messages = queue.Queue()
        self.testing = False
        self.thread = None
        self.closed = False
        self.key = None
        self.local_only = local_only
        self.limit = cycles
        self.interval = interval
        self.passed = self.failed = 0
        self.phase = 'host_start'
        self.phase_started = time.monotonic()
        self.next_check = 0
        self.internet = False
        self.last_state_saved = 0
        self.saved_signature = None
        self.paused = False
        self.root = tk.Tk(); self.root.withdraw()
        core.SETTINGS_PATH = self.base / 'editor-settings.json'
        self.style_editor = core.Editor(self.root, True)
        self.root.withdraw()
        self.root.report_callback_exception = lambda _kind, error, _trace: self.log('GUI ERROR: ' + str(error))
        width = max(720, min(900, (self.root.winfo_screenwidth()-60)//2))
        height = max(660, min(800, self.root.winfo_screenheight()-150))
        facade = SimpleNamespace(root=self.root, plugin_apis={}, documents=[], remember_document=lambda: None)
        self.host_window = tk.Toplevel(self.root)
        self.host_window.title('LIVE TEST — HOST (temporary demo project)')
        self.host_window.geometry(f'{width}x{height}+10+20')
        self.host = ChatPane(self.host_window, self.root, self.base/'host-data', editor=facade)
        self.host.pack(fill='both', expand=True)
        self.host.select_role('Host'); self.host.author.set('Demo Host'); self.host.room.set('Live Test')
        self.host.project.set(str(self.project)); self.host.bind.set('127.0.0.1'); self.host.port.set('0')
        self.client_window = tk.Toplevel(self.root)
        self.client_window.title('LIVE TEST — CLIENT (temporary local copy)')
        x = min(width+30, max(50, self.root.winfo_screenwidth()-width-10))
        self.client_window.geometry(f'{width}x{height}+{x}+20')
        self.client = ChatPane(self.client_window, self.root, self.base/'client-data', editor=facade)
        self.client.pack(fill='both', expand=True)
        self.client.author.set('Demo Client'); self.client.room.set('Live Test'); self.client.project.set(str(self.copy))
        self.control = tk.Toplevel(self.root)
        self.control.title('Team Project — Continuous Test Control')
        self.control.geometry('850x300+40+60')
        body = ttk.Frame(self.control, padding=14); body.pack(fill='both', expand=True)
        self.status = tk.StringVar(value='Starting temporary Host and Client…')
        ttk.Label(body, text='Continuous test — say stop to end', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(body, textvariable=self.status, wraplength=810).pack(anchor='w', pady=6)
        ttk.Label(body, text='Demo files and log: ' + str(self.base), wraplength=810, style='Muted.TLabel').pack(anchor='w')
        actions = ttk.Frame(body); actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Check Now', command=self.check_now).pack(side='left')
        self.pause_button = ttk.Button(actions, text='Pause Checks', command=self.toggle_pause)
        self.pause_button.pack(side='left', padx=6)
        ttk.Button(actions, text='Test Internet Relay', command=self.start_internet).pack(side='left')
        ttk.Button(actions, text='Use Local Connection', command=self.use_local).pack(side='left', padx=6)
        ttk.Button(actions, text='Stop Test', command=lambda:self.request_stop('Stop Test button')).pack(side='right')
        self.output = tk.Text(body, height=6, state='disabled', wrap='word')
        self.output.pack(fill='both', expand=True)
        for window in (self.host_window,self.client_window,self.control):
            window.protocol('WM_DELETE_WINDOW', window.withdraw)
        self.controller_job = self.root.after(100, self.tick)
        self.host.connect(True)
        self.log('Started isolated demo. ' + ('Local-only checks.' if local_only else 'Local checks first, then automatic HTTPS relay test.'))
        (self.base/'test.pid').write_text(str(os.getpid()))
        print('LIVE_TEST_PID=' + str(os.getpid()), flush=True)
        print('LIVE_TEST_FOLDER=' + str(self.base), flush=True)
        self.save_state()

    def log(self, text):
        line = time.strftime('%H:%M:%S') + ' ' + text
        with self.logfile.open('a', encoding='utf-8') as stream: stream.write(line+'\n')
        print(line, flush=True)
        if not self.closed:
            self.output.configure(state='normal'); self.output.insert('end',line+'\n')
            if int(self.output.index('end-1c').split('.')[0]) > 200: self.output.delete('1.0','end-150l')
            self.output.see('end'); self.output.configure(state='disabled')

    def save_state(self):
        signature=(self.phase,self.passed,self.failed,self.internet,self.closed)
        if signature==self.saved_signature and time.monotonic()-self.last_state_saved<5:
            return
        self.saved_signature=signature;self.last_state_saved=time.monotonic()
        atomic_file(self.statefile, json.dumps({'pid':os.getpid(),'phase':self.phase,'passed':self.passed,
                    'failed':self.failed,'internet':self.internet,'folder':str(self.base),'running':not self.closed,
                    'last_update':time.time()}).encode())

    def task(self, kind, function):
        if self.testing: return
        self.testing = True
        def work():
            try: result, error = function(), None
            except Exception as failure: result, error = None, str(failure)
            self.messages.put((kind,result,error))
        self.thread = threading.Thread(target=work,daemon=True); self.thread.start()

    def set_phase(self, phase):
        self.phase=phase; self.phase_started=time.monotonic()

    def join_client(self, address):
        self.client.address.set(address); self.client.token.set(self.key); self.client.select_role('Client')
        self.client.token.set(self.key)
        write_invitation(self.invitation, {'format':'pocket-team-invite','version':1,'server':address,
                         'room':'Live Test','client_name':'Demo Client','client_key':self.key,'project_name':'Demo Project'})
        self.client.connect()
        self.set_phase('client_join')

    def check_now(self):
        self.next_check=0
        self.paused=False; self.pause_button.configure(text='Pause Checks')

    def toggle_pause(self):
        self.paused=not self.paused
        self.pause_button.configure(text='Resume Checks' if self.paused else 'Pause Checks')

    def use_local(self):
        if self.testing or not self.host.connected or not self.key: return
        self.internet=False
        self.client.leave(); self.set_phase('switch_local')

    def start_internet(self):
        if self.testing or not self.host.connected or not self.key: return
        if self.host.relay_active:
            self.client.leave(); self.set_phase('switch_internet')
        else:
            self.host.start_relay(); self.set_phase('relay_start')
            self.log('Starting authenticated HTTPS relay test…')

    def cycle(self):
        number=self.passed+self.failed+1
        address=self.client.address.get(); key=self.key; token=self.host.token.get()
        admin_address=self.host.address.get()
        expected_project=str(self.project.resolve())
        def check():
            import hashlib
            if self.stop_requested.is_set(): return None
            admin=ChatClient(admin_address,token,'Live Test','Demo Host',timeout=5)
            remote=ChatClient(address,key,'Live Test','Demo Client',timeout=5)
            manifest=remote.json_request('/project/manifest')
            expected_id=hashlib.sha256(os.fsencode(expected_project)).hexdigest()
            if manifest.get('project_id')!=expected_id: raise RuntimeError('Selected server is not the temporary test project; checks stopped.')
            marker=f'Automatic check {number} — {time.strftime("%H:%M:%S")}'
            ident=admin.send('[test] Host heartbeat: '+marker)['id']
            assert any(row['id']==ident for row in remote.messages(ident-1)), 'Host-to-client chat missing'
            ident=remote.send('[test] Client heartbeat: '+marker)['id']
            assert any(row['id']==ident for row in admin.messages(ident-1)), 'Client-to-host chat missing'
            copy=ProjectCopy(remote,self.copy,self.base/'checker-state')
            # A new transport URL has a separate baseline. First adopt the
            # already-matching copy, before mutating the host heartbeat.
            initial=copy.pull()
            if initial['conflicts']: raise RuntimeError('Existing project conflicts: '+', '.join(initial['conflicts']))
            atomic_file(self.project/'_automation/server.txt', marker.encode())
            result=copy.pull()
            if result['conflicts']: raise RuntimeError('Project conflicts: '+', '.join(result['conflicts']))
            assert (self.copy/'_automation/server.txt').read_text()==marker, 'Project download mismatch'
            atomic_file(self.copy/'_automation/client.txt', marker.encode())
            result=copy.publish()
            if result['conflicts']: raise RuntimeError('Publish conflicts: '+', '.join(result['conflicts']))
            assert (self.project/'_automation/client.txt').read_text()==marker, 'Project upload mismatch'
            packet=self.base/'check-packet.bin';packet.write_bytes(b'\x00\xff'+marker.encode())
            ident=remote.upload(packet)['id']
            item=next(row for row in remote.messages(ident-1) if row['id']==ident)
            destination=self.base/'received-check-packet.bin';remote.download(item,destination)
            assert destination.read_bytes()==packet.read_bytes(), 'Attachment checksum mismatch'
            return marker
        self.task('cycle',check)

    def tick(self):
        if self.stop_requested.is_set():
            self.shutdown(); return
        try:
            while True:
                kind,result,error=self.messages.get_nowait()
                self.testing=False
                if error:
                    self.failed+=1; self.log('FAIL '+kind+': '+error)
                    self.status.set('Check failed — details in log. You can retry or test manually.')
                    self.next_check=time.monotonic()+self.interval
                    if kind=='invite': self.paused=True;self.set_phase('running')
                elif kind=='invite':
                    self.key=result['key'];self.join_client(self.host.address.get())
                elif kind=='cycle':
                    self.passed+=1
                    self.log(f'PASS {self.passed}: chat both ways, project pull/publish, binary upload/download ({"HTTPS relay" if self.internet else "local"}).')
                    self.status.set(f'{self.passed} passed · {self.failed} failed · {"Internet HTTPS relay" if self.internet else "Local connection"} · next check in {self.interval}s')
                    self.next_check=time.monotonic()+self.interval
                    if self.limit and self.passed>=self.limit: self.request_stop('Cycle limit reached')
                    elif self.passed==1 and not self.local_only and not self.internet: self.start_internet()
        except queue.Empty: pass
        if self.stop_requested.is_set(): self.shutdown();return
        if self.phase=='host_start' and self.host.connected:
            token=self.host.token.get();address=self.host.address.get()
            self.task('invite',lambda:ChatClient(address,token,'Live Test','Host',timeout=5).json_request('/clients',data=json.dumps({'name':'Demo Client'}).encode()))
            self.set_phase('invite')
        elif self.phase=='client_join' and self.client.connected:
            self.log('Client joined and copied the demo project.');self.set_phase('running');self.next_check=0
        elif self.phase=='relay_start':
            self.status.set(self.host.relay_status.get())
            if self.host.relay_active:
                self.client.leave();self.set_phase('switch_internet')
            elif self.host.relay_status.get().startswith('Relay failed:'):
                self.log(self.host.relay_status.get());self.failed+=1;self.set_phase('running');self.local_only=True
        elif self.phase in ('switch_internet','switch_local') and not self.client.connected and not self.client.connecting:
            self.internet=self.phase=='switch_internet'
            self.join_client(self.host.relay_url.get() if self.internet else self.host.address.get())
        elif self.phase=='running' and not self.testing and not self.paused and time.monotonic()>=self.next_check:
            if (not self.host.connected or not self.client.connected or Path(self.host.project.get()).resolve()!=self.project.resolve()
                    or Path(self.client.project.get()).resolve()!=self.copy.resolve()):
                self.paused=True;self.pause_button.configure(text='Resume Checks')
                self.log('Automatic checks paused: connection or selected project changed. Demo data remains untouched.')
            else:self.cycle()
        if self.phase not in ('running','relay_start') and time.monotonic()-self.phase_started>35:
            self.log('Setup is taking longer than expected: '+self.phase);self.phase_started=time.monotonic()
        self.save_state()
        self.controller_job=self.root.after(200,self.tick)

    def request_stop(self, reason):
        self.stop_reason=reason
        self.stop_requested.set()

    def shutdown(self):
        if self.closed:return
        self.log('Stopping live test ('+(self.stop_reason or 'stop requested')+'). Demo files and log are retained at '+str(self.base))
        self.stop_requested.set();self.closed=True;self.phase='stopped';self.save_state()
        self.host.cleanup();self.client.cleanup()
        self.root.quit()

    def run(self):
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda signum,_frame:self.request_stop(signal.Signals(signum).name))
        try:self.root.mainloop()
        finally:
            self.shutdown()
            for worker in (self.thread,self.host.worker.thread,self.client.worker.thread,self.host.relay.thread,self.client.relay.thread):
                if worker:worker.join(timeout=6)
            self.style_editor.quit()
            # Workers retain only data snapshots; release GUI ownership on this
            # thread even if a network request takes longer to wind down.
            for name,value in list(vars(self).items()):
                if isinstance(value,(tk.Misc,tk.Variable)):
                    setattr(self,name,None)
            self.host=self.client=self.style_editor=None
            gc.collect()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-only',action='store_true',help='Skip the automatic public HTTPS relay test.')
    parser.add_argument('--cycles',type=int,default=0,help='Stop after N successful checks; default runs until Stop.')
    parser.add_argument('--interval',type=int,default=30)
    args=parser.parse_args()
    app=LiveTest(args.local_only,args.cycles,max(5,args.interval))
    app.run()

if __name__=='__main__':main()
