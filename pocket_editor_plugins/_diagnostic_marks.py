"""Reusable viewport-aware zigzag diagnostics; all methods run on the Tk thread."""
import tkinter as tk
from pocket_editor_plugins._fusionphp import TextPositions


class DiagnosticMarks:
    def __init__(self,editor,on_click):
        self.editor=editor;self.on_click=on_click;self.canvases=[]
        self.source='';self.issues=[];self.valid=lambda:False;self.signature=None

    def clear(self):
        for canvas in self.canvases:
            if canvas.winfo_exists():canvas.destroy()
        self.canvases=[];self.signature=None;self.issues=[]

    def show(self,source,issues,valid):
        self.clear();self.source=source;self.positions=TextPositions(source);self.issues=list(issues);self.valid=valid;self.draw()

    def draw(self):
        text=self.editor.buffer
        if not self.valid():
            self.clear();return
        signature=(str(text.widget),text.yview(),text.xview(),text.winfo_width(),text.winfo_height(),
                   str(text.cget('font')),tuple(sorted(self.editor.editor_font.actual().items())),
                   text.cget('wrap'),str(text.cget('tabs')),text.cget('background'))
        if signature==self.signature:return
        self.signature=signature
        for canvas in self.canvases:
            if canvas.winfo_exists():canvas.destroy()
        self.canvases=[]
        for issue in self.issues[:50]:
            start=issue['start'];end=min(issue['end'],start+160)
            # Point diagnostics at EOF still receive a visible marker.
            if start>=len(self.source):end=start+1
            groups=[]
            for offset in range(start,max(start+1,end)):
                if offset>start and self.source[offset:offset+1]=='\n':break
                box=text.bbox(self.positions.index(offset))
                if not box:continue
                x,y,width,height=box
                if self.source[offset:offset+1] in ('','\n'):width=6
                if groups and groups[-1][1]==y and x<=groups[-1][2]+1:groups[-1][2]=x+max(6,width)
                else:groups.append([x,y,x+max(6,width),height])
            for x,y,end,height in groups:
                width=max(6,end-x);canvas=tk.Canvas(text.widget,width=width,height=4,
                    background=text.cget('background'),highlightthickness=0,takefocus=False)
                points=[value for step in range(0,width+2,2) for value in (step,1 if step%4==0 else 3)]
                canvas.create_line(*points,fill='#ff5252' if issue.get('severity')=='error' else '#e5b454',width=1)
                canvas.place(x=x,y=y+height-4,width=width,height=4)
                canvas.bind('<Button-1>',lambda event,issue=issue:self.on_click(issue))
                self.canvases.append(canvas)
