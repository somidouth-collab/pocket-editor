#!/usr/bin/env python3
"""Single-purpose POSIX PTY host: fork before creating any Python threads."""
import codecs
import json
import os
from pathlib import Path
import signal
import sys
import threading

for path in (Path(__file__).parent/'.venv'/'lib').glob('python*/site-packages'):
    sys.path.append(str(path))
from ptyprocess import PtyProcessUnicode

process=None
closing=threading.Event()


def stop(*_args):
    closing.set()
    if process is not None:
        try:
            foreground=os.tcgetpgrp(process.fd)
            if foreground>0 and foreground!=os.getpgrp():os.killpg(foreground,signal.SIGKILL)
        except (OSError,ValueError):pass
        try:os.killpg(process.pid,signal.SIGKILL)
        except (OSError,ProcessLookupError):pass


def interrupted(*args):
    stop();raise SystemExit(0)


def emit(kind,value):
    try:sys.stdout.write(json.dumps([kind,value])+'\n');sys.stdout.flush()
    except (OSError,BrokenPipeError):stop()


def main():
    global process
    signal.signal(signal.SIGTERM,interrupted)
    config=json.loads(sys.stdin.readline())
    environment=os.environ.copy();environment.update(TERM='xterm-256color',COLORTERM='truecolor')
    if config['force_color']:environment.update(FORCE_COLOR='1',CLICOLOR_FORCE='1')
    process=PtyProcessUnicode.spawn(config['command'],cwd=config['directory'],env=environment,
                                    dimensions=(config['rows'],config['columns']))
    process.encoding=config['encoding'];process.codec_errors='replace'
    process.decoder=codecs.getincrementaldecoder(config['encoding'])(errors='replace')
    emit('started',process.pid)
    def input_loop():
        try:
            for line in sys.stdin:
                message=json.loads(line)
                if message[0]=='resize':process.setwinsize(message[1],message[2])
                else:
                    data=message[1].encode(config['encoding'],errors='replace')
                    while data and not closing.is_set():data=data[os.write(process.fd,data[:4096]):]
        except (OSError,ValueError):pass
        finally:stop()
    threading.Thread(target=input_loop,daemon=True).start()
    try:
        while not closing.is_set():
            value=process.read(4096)
            if value:emit('data',value)
    except (EOFError,OSError):pass
    finally:
        process.isalive()
        emit('exit',process.exitstatus if process.exitstatus is not None else process.signalstatus)
        stop();process.close(force=True)


if __name__=='__main__':
    try:main()
    except Exception as error:emit('error',str(error));stop()
