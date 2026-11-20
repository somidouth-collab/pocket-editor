"""Shared animated orbit loader; services are GUI-thread-only."""
import itertools
import math
import tkinter as tk


class LoadingIndicator:
    def __init__(self, api):
        self.root = api._editor.root
        self.jobs, self.sequence = {}, itertools.count(1)
        self.timer, self.frame, self.phase = None, None, 0
        self.closed = False
        self.preview_timers = set()
        api.provide_service('start', self.start)
        api.provide_service('update', self.update)
        api.provide_service('stop', self.stop)
        api.provide_service('finish', self.finish)
        api.provide_service('status', lambda: {key: value['message'] for key, value in self.jobs.items()})
        api.add_command('Loading animation preview', self.preview)
        api.on_cleanup(self.cleanup)

    @staticmethod
    def progress_value(progress):
        if progress is None: return None
        value = float(progress)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Progress must be between 0 and 1, or None for an unknown duration.')
        return value

    def start(self, message='Working…', parent=None, progress=None):
        if self.closed: raise RuntimeError('Loading indicator has stopped.')
        progress = self.progress_value(progress)
        if parent is None:
            if self.frame is None or not self.frame.winfo_exists():
                self.frame = tk.Frame(self.root, background='#11161c', highlightbackground='#27323d', highlightthickness=1)
            self.frame.place(relx=1, rely=1, x=-16, y=-36, anchor='se')
            self.frame.lift()
            parent = self.frame
        card = tk.Frame(parent, background='#11161c')
        card.pack(fill='x', padx=10, pady=6)
        canvas = tk.Canvas(card, width=38, height=38, background='#11161c', highlightthickness=0)
        canvas.pack(side='left', padx=(0, 10))
        label = tk.Label(card, text=str(message)[:160], background='#11161c', foreground='#d1dae4', anchor='w', wraplength=360)
        label.pack(side='left', fill='x', expand=True)
        token = next(self.sequence)
        self.jobs[token] = dict(card=card, canvas=canvas, label=label, message=str(message)[:160], progress=progress)
        if self.timer is None: self.tick()
        return token

    def update(self, token, message=None, progress=None):
        value = self.progress_value(progress)
        job = self.jobs.get(token)
        if job is None or not job['card'].winfo_exists(): return False
        if message is not None:
            job['message'] = str(message)[:160]; job['label'].configure(text=job['message'])
        job['progress'] = value
        return True

    def tick(self):
        self.timer = None
        self.phase = (self.phase + 1) % 120
        for token, job in list(self.jobs.items()):
            if not job['card'].winfo_exists():
                self.stop(token); continue
            canvas = job['canvas']; canvas.delete('all')
            canvas.create_oval(5, 5, 33, 33, outline='#27323d', width=2)
            if job['progress'] is not None:
                canvas.create_arc(5, 5, 33, 33, start=90, extent=-359.9 * job['progress'], style='arc', outline='#1597f5', width=3)
            for index in range(10):
                angle = (self.phase * .13) - index * .30
                x, y = 19 + 14 * math.cos(angle), 19 + 14 * math.sin(angle)
                strength = (10-index)/10
                color = '#%02x%02x%02x' % (int(30+30*strength), int(90+80*strength), int(155+95*strength))
                radius = 1.2 + 1.3*strength
                canvas.create_oval(x-radius,y-radius,x+radius,y+radius,fill=color,outline='')
            pulse = 2.1 + .6*math.sin(self.phase * .13)
            canvas.create_oval(19-pulse,19-pulse,19+pulse,19+pulse,fill='#1597f5',outline='')
        if self.jobs and not self.closed: self.timer = self.root.after(40, self.tick)
        elif self.frame is not None and self.frame.winfo_exists(): self.frame.place_forget()

    def finish(self, token, widget=None, ready=None):
        """Keep animating until readiness, layout, and a display frame complete.

        stop() remains immediate for cancellation. No duration guesses are used
        for work completion; asynchronous callers may supply a ready predicate.
        """
        job = self.jobs.get(token)
        if job is None:return False
        if job.get('finishing'):return True
        job['finishing'] = True
        def wait():
            job['finish_job'] = None
            if self.closed or self.jobs.get(token) is not job:return
            try:
                if widget is not None and not widget.winfo_exists():
                    self.stop(token);return
                if (ready is not None and not ready()) or (widget is not None and not widget.winfo_ismapped()):
                    job['finish_job'] = self.root.after(16,wait);return
                self.root.update_idletasks()
                if self.jobs.get(token) is job:
                    job['finish_job'] = self.root.after(16,lambda:self.stop(token))
            except Exception:
                self.stop(token)
                raise
        job['finish_job'] = self.root.after_idle(wait)
        return True

    def stop(self, token):
        job = self.jobs.pop(token, None)
        if job is None: return False
        if job.get('finish_job') is not None:self.root.after_cancel(job['finish_job'])
        if job['card'].winfo_exists(): job['card'].destroy()
        if self.frame is not None and self.frame.winfo_exists() and not self.frame.winfo_children():self.frame.place_forget()
        if not self.jobs:
            if self.timer is not None:
                self.root.after_cancel(self.timer); self.timer = None
            if self.frame is not None and self.frame.winfo_exists(): self.frame.place_forget()
        return True

    def preview(self):
        token = self.start('Preparing your workspace…')
        def finish():
            self.preview_timers.discard(timer)
            self.stop(token)
        timer = self.root.after(2400, finish)
        self.preview_timers.add(timer)

    def cleanup(self):
        self.closed = True
        for timer in self.preview_timers: self.root.after_cancel(timer)
        self.preview_timers.clear()
        for token in list(self.jobs): self.stop(token)
        if self.frame is not None and self.frame.winfo_exists(): self.frame.destroy()


def register(api):
    LoadingIndicator(api)
