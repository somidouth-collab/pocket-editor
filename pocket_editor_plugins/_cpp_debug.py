"""Owned GDB/MI session. All protocol I/O runs outside the Tk thread."""
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import os
from pocket_editor_plugins._cpp_language import executable


def mi_string(text, start=0):
    if text[start:start+1] != '"':raise ValueError('Expected a debugger string.')
    result = [];index = start+1
    escapes = {'n':'\n', 'r':'\r', 't':'\t', 'b':'\b', 'f':'\f', 'v':'\v', 'a':'\a'}
    while index < len(text):
        char = text[index];index += 1
        if char == '"':return ''.join(result), index
        if char == '\\':
            if index >= len(text):break
            char = text[index];index += 1
            if char in '01234567':
                digits = char
                while index < len(text) and len(digits) < 3 and text[index] in '01234567':digits += text[index];index += 1
                result.append(chr(int(digits, 8)));continue
            char = escapes.get(char, char)
        result.append(char)
    raise ValueError('Incomplete debugger string.')


def mi_fields(text):
    """Parse MI tuples/lists, including repeated frame= entries in stack lists."""
    index = 0
    def value(depth=0):
        nonlocal index
        if depth > 24:raise ValueError('Debugger response is too deeply nested.')
        if index >= len(text):raise ValueError('Incomplete debugger response.')
        if text[index] == '"':
            result, index = mi_string(text, index);return result
        opening = text[index]
        if opening in '{[':
            index += 1;closing = '}' if opening == '{' else ']'
            result = {} if opening == '{' else []
            while index < len(text) and text[index] != closing:
                match = re.match(r'[A-Za-z_][\w-]*=', text[index:])
                key = None
                if match:key = match[0][:-1];index += len(match[0])
                item = value(depth+1)
                if isinstance(result, dict):
                    if key is None:raise ValueError('Invalid debugger tuple.')
                    result[key] = item
                else:result.append({key:item} if key is not None else item)
                if index < len(text) and text[index] == ',':index += 1
                elif index < len(text) and text[index] != closing:raise ValueError('Invalid debugger separator.')
            if index >= len(text):raise ValueError('Incomplete debugger tuple.')
            index += 1;return result
        match = re.match(r'[^,\]}]+', text[index:])
        if not match:raise ValueError('Invalid debugger value.')
        index += len(match[0]);return match[0]
    result = {}
    while index < len(text):
        match = re.match(r'([\w-]+)=', text[index:])
        if not match:raise ValueError('Invalid debugger result.')
        index += len(match[0]);result[match[1]] = value()
        if index < len(text):
            if text[index] != ',':raise ValueError('Invalid debugger separator.')
            index += 1
    return result


class Debugger:
    def __init__(self, program, arguments, directory, settings, breakpoints=()):
        self.program = str(Path(program).resolve());self.arguments = list(arguments)
        self.directory = str(directory);self.settings = dict(settings);self.breakpoints = list(breakpoints)
        self.events = queue.Queue(maxsize=256);self.replies = queue.Queue(maxsize=128)
        self.commands = queue.Queue(maxsize=32);self.stopped = threading.Event()
        self.process = None;self.sequence = 0;self.break_ids = {}
        self.thread = threading.Thread(target=self._run, daemon=True);self.thread.start()

    def publish(self, kind, value):
        try:self.events.put_nowait((kind,value))
        except queue.Full:
            try:self.events.get_nowait()
            except queue.Empty:pass
            try:self.events.put_nowait((kind,value))
            except queue.Full:pass

    def submit(self, kind, value=None):
        self.commands.put_nowait((kind,value))

    def _read(self):
        try:
            while not self.stopped.is_set():
                line = self.process.stdout.readline(1024*1024)
                if not line:break
                line = line.rstrip('\r\n')
                match = re.match(r'^(\d*)\^([\w-]+)(?:,(.*))?$', line)
                if match:
                    payload = mi_fields(match[3] or '')
                    self.replies.put((int(match[1] or 0), match[2], payload), timeout=1)
                elif line.startswith('*stopped'):
                    payload = mi_fields(line.partition(',')[2]);self.publish('stopped',payload)
                    if not str(payload.get('reason','')).startswith('exited'):
                        try:self.commands.put_nowait(('inspect',None))
                        except queue.Full:pass
                elif line.startswith('*running'):self.publish('running',{})
                elif line.startswith(('~"','@"','&"')):
                    self.publish('output',mi_string(line,1)[0])
                elif line and not line.startswith(('=', '(gdb)', '+')):self.publish('output',line+'\n')
        except (OSError, ValueError, queue.Full) as error:
            if not self.stopped.is_set():self.publish('error',str(error))

    def request(self, command):
        self.sequence += 1;ticket = self.sequence
        self.process.stdin.write(str(ticket)+command+'\n');self.process.stdin.flush()
        deadline = time.monotonic()+12
        while not self.stopped.is_set() and time.monotonic() < deadline:
            if self.process.poll() is not None:raise RuntimeError('GDB stopped.')
            try:ident, status, payload = self.replies.get(timeout=.05)
            except queue.Empty:continue
            if ident != ticket:continue
            if status == 'error':raise RuntimeError(payload.get('msg','Debugger command failed.'))
            return payload
        raise RuntimeError('Debugger command stopped or timed out.')

    def _run(self):
        quote = lambda value:json.dumps(str(value), ensure_ascii=False)
        try:
            command = [executable('gdb',self.settings['gdb']), '--quiet','--nx','--interpreter=mi2',
                       '-iex','set auto-load off']
            self.process = subprocess.Popen(command, cwd=self.directory, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.reader = threading.Thread(target=self._read,daemon=True);self.reader.start()
            for command in ('-gdb-set pagination off','-gdb-set confirm off','-gdb-set mi-async on',
                            '-gdb-set startup-with-shell off', '-file-exec-and-symbols '+quote(self.program)):
                self.request(command)
            if self.arguments:self.request('-exec-arguments '+' '.join(quote(arg) for arg in self.arguments))
            for location in self.breakpoints:
                answer = self.request('-break-insert '+quote(location))
                self.break_ids[location] = answer['bkpt']['number']
            if not self.breakpoints:self.request('-break-insert -t main')
            self.request('-exec-run')
            while not self.stopped.is_set():
                try:kind, value = self.commands.get(timeout=.1)
                except queue.Empty:
                    if self.process.poll() is not None:break
                    continue
                try:
                    if kind == 'inspect':
                        self.publish('stack',self.request('-stack-list-frames').get('stack',[]))
                        self.publish('variables',self.request('-stack-list-variables --simple-values').get('variables',[]))
                    elif kind == 'watch':self.publish('watch',(value,self.request('-data-evaluate-expression '+quote(value)).get('value','')))
                    elif kind == 'frame':
                        self.request('-stack-select-frame '+str(int(value)))
                        self.publish('variables',self.request('-stack-list-variables --simple-values').get('variables',[]))
                    elif kind == 'breakpoint':
                        if value in self.break_ids:self.request('-break-delete '+self.break_ids.pop(value))
                        else:self.break_ids[value] = self.request('-break-insert '+quote(value))['bkpt']['number']
                    else:
                        command = {'continue':'-exec-continue','next':'-exec-next','step':'-exec-step',
                                   'finish':'-exec-finish','pause':'-exec-interrupt --all'}[kind]
                        self.request(command)
                except (RuntimeError, ValueError, KeyError) as error:self.publish('error',str(error))
        except Exception as error:
            if not self.stopped.is_set():self.publish('error',str(error))
        finally:
            process = self.process
            if process:
                if process.poll() is None:
                    try:
                        process.stdin.write('999999-gdb-exit\n');process.stdin.flush();process.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        process.terminate()
                        try:process.wait(timeout=1)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)
                for stream in (process.stdin,process.stdout):
                    if stream:stream.close()
            self.publish('closed',None)

    def close(self):self.stopped.set()
