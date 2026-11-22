"""Offline tooling for FusionPHP Template v2; never renders PHP or application code."""
from dataclasses import dataclass
from pathlib import Path
import re
import os
from bisect import bisect_left, bisect_right

MAX_SOURCE = 300000
FIELD = r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*'
NAME = r'[A-Za-z_][A-Za-z0-9_]*'
HTML = tuple('html head body title meta link main header footer nav section article aside div span p h1 h2 h3 h4 h5 h6 a ul ol li dl dt dd strong em b i small pre code blockquote br hr img form label input textarea button select option table caption thead tbody tfoot tr th td'.split())
VOID = {'meta','link','br','hr','img','input'}
VALUE_ATTRS = {'class','id','title','alt','name','value','placeholder','aria-label'}
URL_ATTRS = {'href','src','action','formaction'}
HELPERS = {'trim','length','default','join'}
COMMANDS = {'extends','block','endblock','yield','if','else','endif','for','in','endfor'}
PATTERN = re.compile(r'''<!--[\s\S]*?-->|<!doctype html\s*>|</?[a-z][\w-]*(?:"[^"]*"|'[^']*'|[^"'<>])*?>|\{\{(?:"[^"]*"|'[^']*'|(?!\}\})[^"'])*?\}\}|\{%[\s\S]*?%\}|(?:(?!<|\{\{|\{%)[\s\S])+''',re.I|re.A)
ATTR = re.compile(r'''\s+[a-z_:][\w:.-]*(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>]+))?''',re.I|re.A)
EXPRESSION = re.compile(r'''\{\{(?:"[^"]*"|'[^']*'|(?!\}\})[^"'])*?\}\}''')


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    raw: str
    expressions: tuple = ()


class TemplateError(ValueError):
    def __init__(self,message,start,end=None):
        super().__init__(message);self.start=start;self.end=max(start+1,end or start+1)


def is_fpf(path):return path is not None and Path(path).suffix.lower()=='.fpf'


def layout_mode(path,mode='Auto'):
    return mode=='Layout' or (mode=='Auto' and path is not None and Path(path).parent.name=='layouts')


def expression_valid(value,url=False):
    if url:return re.fullmatch(r'url\s+('+FIELD+')',value) is not None
    return (re.fullmatch(FIELD,value) is not None or
            re.fullmatch(r'(trim|length)\s+('+FIELD+')',value) is not None or
            re.fullmatch(r'''(default|join)\s+('''+FIELD+r''')\s+(?:"[^"]*"|'[^']*')''',value) is not None)


def tag_expressions(raw,start):
    name=re.match(r'</?([a-z][\w-]*)',raw,re.I)
    if name and name[1].lower() not in HTML:
        raise TemplateError('Unsupported HTML element '+name[1]+'. FusionPHP uses a restricted HTML vocabulary.',start+name.start(1),start+name.end(1))
    if '{%' in raw:raise TemplateError('Directives belong between HTML tags, not inside attributes.',start+raw.index('{%'),start+len(raw))
    if '{{' not in raw:return ()
    opening=re.match(r'<[a-z][\w-]*',raw,re.I)
    if not opening:raise TemplateError('Invalid attribute interpolation.',start,start+len(raw))
    position=opening.end();expressions=[]
    while True:
        attr=ATTR.match(raw,position)
        if not attr:break
        value=attr[0];position=attr.end()
        if '{{' not in value:continue
        dynamic=re.fullmatch(r'''(\s+([\w-]+)\s*=\s*)(["'])\{\{(.*?)\}\}\3''',value,re.S)
        if not dynamic:raise TemplateError('Use a single expression as the entire quoted attribute value.',start+attr.start(),start+attr.end())
        attribute=dynamic[2].lower()
        if attribute not in VALUE_ATTRS|URL_ATTRS:
            raise TemplateError('Unsupported dynamic attribute '+attribute+'.',start+attr.start(),start+attr.end())
        offset=start+attr.start()+value.index('{{')
        expressions.append(Token('url' if attribute in URL_ATTRS else 'value',dynamic[4].strip(),offset,
                                 start+attr.end()-1,value[value.index('{{'):-1]))
    if not re.fullmatch(r'\s*/?>',raw[position:]):
        raise TemplateError('Invalid interpolated HTML tag.',start+position,start+len(raw))
    return tuple(expressions)


def tokenize(source):
    if len(source)>MAX_SOURCE:raise ValueError('FusionPHP tools support documents up to 300,000 characters.')
    tokens=[];position=0
    while position<len(source):
        match=PATTERN.match(source,position)
        if not match:raise TemplateError('Malformed template or HTML. Check closing delimiters, quotes, or escape a literal < as &lt;.',position,position+2)
        raw=match[0];extra=()
        if raw.startswith('<'):
            extra=tag_expressions(raw,position);kind='comment' if raw.startswith('<!--') else 'html';value=raw
        elif raw.startswith('{{'):kind='value';value=raw[2:-2].strip()
        elif raw.startswith('{%'):kind='command';value=raw[2:-2].strip()
        else:kind='text';value=raw
        tokens.append(Token(kind,value,position,match.end(),raw,extra));position=match.end()
    return tokens


class Compiler:
    def __init__(self,source,is_layout=False):self.source=source;self.tokens=tokenize(source);self.index=0;self.is_layout=is_layout
    def whitespace(self):
        while self.index<len(self.tokens) and self.tokens[self.index].kind=='text' and not self.tokens[self.index].value.strip():self.index+=1
    def fail(self,message,token=None):
        token=token or (self.tokens[-1] if self.tokens else Token('text','',0,1,''))
        raise TemplateError(message,token.start,token.end)
    def compile(self):
        self.whitespace()
        first=self.tokens[self.index] if self.index<len(self.tokens) else None
        extends=re.fullmatch(r'extends "([A-Za-z0-9_-]+)"',first.value) if first and first.kind=='command' else None
        if extends:
            if self.is_layout:self.fail('Layouts cannot extend other layouts.',first)
            self.index+=1;self.whitespace();names=set()
            while self.index<len(self.tokens):
                token=self.tokens[self.index]
                block=re.fullmatch(r'block ('+NAME+')',token.value) if token.kind=='command' else None
                if not block or block[1] in names:self.fail('Expected a unique named block after extends.',token)
                names.add(block[1]);self.index+=1;self.sequence({'endblock'},False,0);self.whitespace()
        self.sequence(set(),self.is_layout,0)
        return self.tokens
    def sequence(self,stops,allow_yield,depth):
        while self.index<len(self.tokens):
            token=self.tokens[self.index];self.index+=1
            values=token.expressions if token.kind in ('html','comment') else (token,) if token.kind=='value' else ()
            for expression in values:
                if not expression_valid(expression.value,expression.kind=='url'):
                    self.fail('URL attributes require {{ url field }}.' if expression.kind=='url' else
                              'Expected a data field or a supported helper with valid arguments (trim, length, default, join).',expression)
            if token.kind!='command':continue
            if token.value in stops:return token.value
            if allow_yield and re.fullmatch(r'yield '+NAME,token.value):continue
            if depth>=64:self.fail('Template nesting limit exceeded (64).',token)
            if re.fullmatch(r'if '+FIELD,token.value):
                stop=self.sequence({'else','endif'},allow_yield,depth+1)
                if stop=='else':self.sequence({'endif'},allow_yield,depth+1)
            elif re.fullmatch(r'for '+NAME+' in '+FIELD,token.value):self.sequence({'endfor'},allow_yield,depth+1)
            else:self.fail('Unexpected directive '+token.value+'.',token)
        if stops:self.fail('Expected '+' or '.join(sorted(stops))+'.')


def check_source(source,path=None,mode='Auto'):
    try:Compiler(source,layout_mode(path,mode)).compile();return []
    except TemplateError as error:
        position=min(error.start,len(source));line=source.count('\n',0,position)+1
        return [dict(start=position,end=min(error.end,len(source)),line=line,
                     column=position-source.rfind('\n',0,position),severity='error',message=str(error))]


def format_source(source,path=None,indent=4,mode='Auto'):
    if type(indent) is not int or not 1<=indent<=8:raise ValueError('Indent width must be 1–8 spaces.')
    tokens=Compiler(source,layout_mode(path,mode)).compile()
    # Reindent structure only. Never wrap text, rewrite expressions, or touch raw-text regions.
    protected=[];raw_stack=[]
    for token in tokens:
        if '\n' in token.raw and token.kind!='text':protected.append((token.start,token.end))
        if token.kind!='html':continue
        tag=re.match(r'<(/?)([a-z][\w-]*)',token.raw,re.I)
        if not tag:continue
        if tag[2].lower() in ('pre','textarea','code'):
            if not tag[1] and not token.raw.rstrip().endswith('/>'):raw_stack.append((tag[2].lower(),token.start))
            elif tag[1] and raw_stack and raw_stack[-1][0]==tag[2].lower():
                _,begin=raw_stack.pop();protected.append((begin,token.end))
    protected.extend((begin,len(source)) for _,begin in raw_stack)
    protected.sort();protected_index=0
    html=[];directives=[];position=0;index=0;output=[]
    for line in source.splitlines(keepends=True):
        end=position+len(line);current=[]
        while index<len(tokens) and tokens[index].start<end:
            token=tokens[index];index+=1;current.append(token)
        prefix=len(line)-len(line.lstrip(' \t'));content=line[prefix:]
        level=len(html)+len(directives)
        first=next((token for token in current if token.start==position+prefix),None)
        if first:
            if first.kind=='html' and first.raw.startswith('</'):level-=1
            if first.kind=='command' and first.value in ('else','endif','endfor','endblock'):level-=1
        while protected_index<len(protected) and protected[protected_index][1]<=position:protected_index+=1
        keep=protected_index<len(protected) and protected[protected_index][0]<end
        if content.strip() and first and first.kind in ('html','comment','command','value') and not keep:
            output.append(' '*(min(64,max(0,level))*indent)+content)
        else:output.append(line)
        for token in current:
            if token.kind=='html':
                tag=re.match(r'<(/?)([a-z][\w-]*)',token.raw,re.I)
                if tag:
                    closing,name=tag[1],tag[2].lower()
                    if closing:
                        if name in html:del html[html.index(name):]
                    elif name not in VOID and not token.raw.rstrip().endswith('/>'):html.append(name)
            elif token.kind=='command':
                command=token.value.split(' ',1)[0]
                if command in ('if','for','block'):directives.append(command)
                elif command in ('endif','endfor','endblock') and directives:directives.pop()
        position=end
    return ''.join(output)


def highlight_spans(source):
    for token in PATTERN.finditer(source):
        raw=token[0];start=token.start()
        if raw.startswith('<!--'):yield 'comment',start,token.end();continue
        if raw.startswith('<'):
            name=re.match(r'</?([a-z][\w-]*)',raw,re.I)
            if name:yield 'tag',start+name.start(1),start+name.end(1)
            position=name.end() if name else len(raw)
            while True:
                attr=ATTR.match(raw,position)
                if not attr:break
                attribute=re.match(r'\s+([a-z_:][\w:.-]*)',attr[0],re.I)
                if attribute:yield 'attribute',start+attr.start()+attribute.start(1),start+attr.start()+attribute.end(1)
                for string in re.finditer(r'''"[^"]*"|'[^']*' ''',attr[0],re.X):yield 'string',start+attr.start()+string.start(),start+attr.start()+string.end()
                position=attr.end()
            expressions=list(EXPRESSION.finditer(raw))
        elif raw.startswith(('{{','{%')):expressions=[re.match(r'[\s\S]+',raw)]
        else:continue
        for expression in expressions:
            value=expression[0];base=start+expression.start()
            for part in re.finditer(r'''"[^"]*"|'[^']*'|[A-Za-z_][A-Za-z0-9_.]*|\d+''',value):
                word=part[0]
                kind='string' if word.startswith(('"',"'")) else 'keyword' if word in COMMANDS else 'function' if word in HELPERS|{'url'} else 'number' if word.isdigit() else 'field'
                yield kind,base+part.start(),base+part.end()


def find_project(path,selected=None):
    path=Path(path).resolve()
    if selected:
        root=Path(selected).resolve()
        if path.is_relative_to(root) and (root/'app').is_dir():return root
    for root in path.parents:
        if (root/'fusionphp/core/Template.php').is_file():return root
    return path.parent


def php_fields(source):
    # Static array-key extraction only: no PHP evaluation, autoloading, config or environment reads.
    pattern=re.compile(r'''//[^\n]*|/\*[\s\S]*?\*/|\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|=>|[\[\]]|[^\s\[\]"']+''')
    tokens=[token[0] for token in pattern.finditer(source) if not token[0].startswith(('//','/*','#'))]
    fields=set();stack=[];pending=None
    for index,token in enumerate(tokens):
        if token.startswith(('"',"'")) and index+1<len(tokens) and tokens[index+1]=='=>':
            name=token[1:-1]
            if re.fullmatch(NAME,name):
                prefix=stack[-1] if stack else ''
                pending=(prefix+'.' if prefix else '')+name;fields.add(pending)
        elif token=='[':stack.append(pending or (stack[-1] if stack else ''));pending=None
        elif token==']':
            if stack:stack.pop()
            pending=None
        elif token!='=>':pending=None
    return fields


def project_symbols(root):
    root=Path(root).resolve();fields=set();layouts=set();used=0;count=0
    app=root/'app'
    if app.is_dir():
        for directory,folders,files in os.walk(app,followlinks=False):
            folders[:]=[name for name in sorted(folders) if name not in ('vendor','node_modules','storage','.git') and not (Path(directory)/name).is_symlink()]
            for name in sorted(files):
                if not name.endswith(('Model.php','Controller.php')):continue
                path=Path(directory)/name
                if path.is_symlink() or not path.resolve().is_relative_to(app):continue
                try:
                    size=path.stat().st_size
                    if size>200000 or used+size>2000000 or count>=40:continue
                    fields.update(php_fields(path.read_text(encoding='utf-8')));used+=size;count+=1
                except (OSError,UnicodeError):continue
        directory=app/'layouts'
        if directory.is_dir() and not directory.is_symlink():
            for path in sorted(directory.glob('*.fpf'))[:100]:
                if not path.is_symlink() and re.fullmatch(r'[A-Za-z0-9_-]+',path.stem):layouts.add(path.stem)
    return fields,layouts


class TextPositions:
    """Build once for batched highlighting; offsets stay in Python code points."""
    def __init__(self,source):
        self.length=len(source)
        self.lines=[0]+[index+1 for index,char in enumerate(source) if char=='\n']
        self.wide=[index for index,char in enumerate(source) if ord(char)>0xffff]
    def index(self,offset):
        offset=max(0,min(offset,self.length));line=bisect_right(self.lines,offset)-1;begin=self.lines[line]
        column=offset-begin+bisect_left(self.wide,offset)-bisect_left(self.wide,begin)
        return f'{line+1}.{column}'


def text_index(source,offset):
    """Map Python offsets to Tk's UTF-16 columns without splitting emoji."""
    prefix=source[:max(0,min(offset,len(source)))]
    return f"{prefix.count(chr(10))+1}.{len(prefix.rsplit(chr(10),1)[-1].encode('utf-16-le'))//2}"


def completion_items(source,cursor,path,root=None,mode='Auto',indent_width=4,extra_fields=()):
    from pocket_editor_plugins._php_tools import position_at
    before=source[:cursor]
    if before.rfind('<!--')>before.rfind('-->'):return []
    fields,layouts=project_symbols(root or Path(path).parent)
    fields.update(value for value in extra_fields if re.fullmatch(FIELD,value))
    for expression in EXPRESSION.finditer(source):
        if expression.start()<=cursor<=expression.end():continue
        for match in re.finditer(r'''"[^"]*"|'[^']*'|'''+FIELD,expression[0][2:-2]):
            word=match[0]
            if not word.startswith(('"',"'")) and word not in HELPERS|{'url'}:fields.add(word)
    commands=list(re.finditer(r'\{%([\s\S]*?)%\}',before));loops=[];blocks=[]
    for match in commands:
        value=match[1].strip();loop=re.fullmatch(r'for ('+NAME+') in ('+FIELD+')',value)
        if loop:loops.append((loop[1],loop[2]));blocks.append('for')
        elif re.fullmatch(r'if '+FIELD,value):blocks.append('if')
        elif re.fullmatch(r'block '+NAME,value):blocks.append('block')
        elif value in ('endif','endfor','endblock'):
            if blocks:blocks.pop()
            if value=='endfor' and loops:loops.pop()
    for name,collection in loops:
        fields.add(name)
        fields.update(name+value[len(collection):] for value in tuple(fields) if value.startswith(collection+'.'))
    result=[]
    def add(label,text,start,end,snippet=False):
        result.append(dict(label=label,insertTextFormat=2 if snippet else 1,
            textEdit={'range':{'start':position_at(source,start),'end':position_at(source,end)},'newText':text}))
    opening=before.rfind('{%')
    if opening>before.rfind('%}'):
        content=before[opening+2:];prefix=content.strip();closing=source.find('%}',cursor)
        end=closing+2 if closing!=-1 and '\n' not in source[cursor:closing] else cursor
        if re.fullmatch(r'[A-Za-z]*',prefix):
            pad=re.match(r'[ \t]*',before[before.rfind('\n')+1:])[0];inner=pad+' '*indent_width
            snippets={'if':'if ${1:condition} %}\n'+inner+'$0\n'+pad+'{% endif %}',
                      'for':'for ${1:item} in ${2:items} %}\n'+inner+'$0\n'+pad+'{% endfor %}'}
            if not source[:opening].strip() and not layout_mode(path,mode):snippets['extends']='extends "${1:'+next(iter(sorted(layouts)),'main')+'}" %}'
            if re.match(r'\s*\{%\s*extends ',source):snippets['block']='block ${1:content} %}\n'+inner+'$0\n'+pad+'{% endblock %}'
            if layout_mode(path,mode):snippets['yield']='yield ${1:content} %}'
            if blocks:
                close={'if':'endif','for':'endfor','block':'endblock'}[blocks[-1]]
                snippets[close]=close+' %}'
                if blocks[-1]=='if':snippets['else']='else %}'
            for label,text in snippets.items():
                if label.startswith(prefix):add(label,' '+text,opening+2,end,True)
        elif re.match(r'\s*extends\s+"[^"\n]*$',content):
            prefix=content.rsplit('"',1)[-1]
            for name in sorted(layouts):
                if name.startswith(prefix):add(name,name,cursor-len(prefix),cursor)
        else:
            prefix=re.search(r'[A-Za-z_][A-Za-z0-9_.]*$|$',before)[0]
            for field in sorted(fields):
                if field.startswith(prefix):add(field,field,cursor-len(prefix),cursor)
        return result[:100]
    opening=before.rfind('{{')
    if opening>before.rfind('}}'):
        content=before[opening+2:];prefix=re.search(r'[A-Za-z_][A-Za-z0-9_.]*$|$',before)[0]
        # Completion inside helper string literals would insert field names into data.
        if any(len(part[0])==1 for part in re.finditer(r'''"[^"]*"|'[^']*'|["']''',content)):return []
        tag=before.rfind('<')
        url_context=(tag>before.rfind('>') and re.search(r'''(?:href|src|action|formaction)\s*=\s*["']\{\{[^}]*$''',before[tag:],re.I) is not None)
        closing=source.find('}}',cursor);words=content.strip().split()
        suffix=' }}' if closing<0 and (len(words)<=1 or words[0] in ('url','trim','length')) else ''
        for field in sorted(fields):
            if field.startswith(prefix) and (not url_context or re.match(r'\s*url\s+',content)):
                add(field,field+suffix,cursor-len(prefix),cursor)
        if re.fullmatch(r'\s*[A-Za-z]*',content):
            helpers={'trim':'trim ${1:field}','length':'length ${1:items}',"default":"default ${1:field} '${2:fallback}'","join":"join ${1:items} '${2:, }'"}
            tag=before.rfind('<')
            if tag>before.rfind('>') and re.search(r'''(?:href|src|action|formaction)\s*=\s*["']\{\{[^}]*$''',before[tag:],re.I):helpers={'url':'url ${1:link}'}
            for label,value in helpers.items():
                if label.startswith(prefix):add(label,value+(' }}' if closing<0 else ''),cursor-len(prefix),cursor,True)
        return result[:100]
    tag=before.rfind('<')
    if tag>before.rfind('>'):
        partial=before[tag:];match=re.fullmatch(r'</?([A-Za-z]*)',partial)
        if match:
            for name in HTML:
                if name.startswith(match[1].lower()):add(name,name+('' if source[cursor:cursor+1]=='>' else '>'),cursor-len(match[1]),cursor)
        elif not partial.startswith('</'):
            match=re.search(r'\s([A-Za-z-]*)$',partial)
            if match:
                for name in sorted(VALUE_ATTRS|URL_ATTRS):
                    if name.startswith(match[1]):add(name,name+'="${1:value}"',cursor-len(match[1]),cursor,True)
    return result[:100]
