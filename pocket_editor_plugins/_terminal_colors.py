"""Portable terminal appearance and incremental ANSI SGR decoding."""
import codecs
import re

PALETTE = ['#000000', '#cd3131', '#0dbc79', '#e5e510', '#2472c8', '#bc3fbc', '#11a8cd', '#e5e5e5',
           '#666666', '#f14c4c', '#23d18b', '#f5f543', '#3b8eea', '#d670d6', '#29b8db', '#ffffff']
DEFAULTS = dict(background='#1e1e1e', foreground='#cccccc', command='#3794ff', status='#969696',
                error='#f48771', font_family='', font_size=11, encoding='utf-8', ansi=True, force_color=False,
                palette=PALETTE)
PRESETS = {'Editor Dark': dict(background='#1e1e1e', foreground='#cccccc', command='#3794ff', status='#969696', error='#f48771'),
           'Light': dict(background='#ffffff', foreground='#24292f', command='#0550ae', status='#57606a', error='#b42318'),
           'High Contrast': dict(background='#000000', foreground='#ffffff', command='#6fc3ff', status='#c0c0c0', error='#ff8080')}


# Tango colors are documented in the GNOME color-palette reference.
TANGO = ['#2e3436', '#cc0000', '#4e9a06', '#c4a000', '#3465a4', '#75507b', '#06989a', '#d3d7cf',
         '#555753', '#ef2929', '#8ae234', '#fce94f', '#729fcf', '#ad7fa8', '#34e2e2', '#eeeeec']
GRAPHITE = ['#282c34', '#e06c75', '#98c379', '#e5c07b', '#61afef', '#c678dd', '#56b6c2', '#dcdfe4',
            '#707880', '#ff7a85', '#b5e890', '#ffd68a', '#85c7ff', '#dfa0f5', '#7edce5', '#ffffff']
PRESETS['GNOME Tango'] = dict(background='#2e3436', foreground='#d3d7cf', command='#729fcf',
                              status='#a6aaa1', error='#ef2929', palette=TANGO)
PRESETS['Modern Graphite'] = dict(background='#1b1d23', foreground='#dcdfe4', command='#61afef',
                                  status='#abb2bf', error='#e06c75', palette=GRAPHITE)
for name, colors in PRESETS.items():
    palette = colors.get('palette', PALETTE)
    colors.update(warning=palette[11], success=palette[10])
PRESETS['Light'].update(warning='#9a6700', success='#1a7f37')
DEFAULTS['categorize'] = True
DEFAULTS.update(PRESETS['Modern Graphite'])


def validate_settings(saved):
    if not isinstance(saved, dict):
        raise ValueError('Terminal settings must be an object.')
    result = {key: saved.get(key, value) for key, value in DEFAULTS.items()}
    for key in ('background', 'foreground', 'command', 'status', 'error', 'warning', 'success'):
        if not isinstance(result[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', result[key]):
            raise ValueError(f'{key} needs a color such as #1e1e1e.')
    palette = result['palette']
    if not isinstance(palette, list) or len(palette) != 16 or not all(isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color) for color in palette):
        raise ValueError('The ANSI palette needs 16 hexadecimal colors.')
    result['palette'] = list(palette)
    if type(result['font_size']) is not int or not 8 <= result['font_size'] <= 32:
        raise ValueError('Font size must be between 8 and 32.')
    if not isinstance(result['font_family'], str) or len(result['font_family']) > 100:
        raise ValueError('Choose a font family or leave it blank for the system monospace font.')
    for key in ('ansi', 'force_color', 'categorize'):
        if type(result[key]) is not bool:
            raise ValueError(f'{key} must be a boolean.')
    if not isinstance(result['encoding'], str):
        raise ValueError('Choose an output encoding.')
    if result['encoding'] != 'locale':
        try:
            sample = codecs.getincrementaldecoder(result['encoding'])(errors='replace').decode(b'')
            if not isinstance(sample, str):
                raise ValueError('Not a text codec')
        except (LookupError, ValueError, TypeError, AssertionError):
            raise ValueError('Unknown output encoding.') from None
    return result


CATEGORY_RULES = (
    ('error', re.compile(r'(?i)(?:^\s*(?:\[error\]|error\b|fatal\b|exception\b|traceback\b|failed\b|failure\b)|:\s*(?:fatal\s+)?error\b)')),
    ('warning', re.compile(r'(?i)(?:^\s*(?:\[warn(?:ing)?\]|warn(?:ing)?\b)|:\s*warning\b)')),
    ('success', re.compile(r'(?i)^\s*(?:\[(?:ok|success|passed)\]|success\b|ok\b|passed\b|done\b|(?:build|tests?)\s+(?:succeeded|passed|complete(?:d)?)\b)')),
    ('status', re.compile(r'(?i)^\s*(?:\[(?:system|info|debug)\]|(?:system|info|debug)\s*:)')),
    ('command', re.compile(r'(?i)^\s*(?:\[(?:user|input)\]|(?:user|input)\s*:|\$\s|PS\s+[^>]+>)')),
)


def classify_line(text):
    # Heuristics for uncolored output, never a claim about stdout/stderr origin.
    stripped = re.sub(r'^\s*\[\d[^]]*\]\s*', '', text)
    return next((name for name, pattern in CATEGORY_RULES if pattern.search(stripped)), None)


def color_value(value, settings, default):
    if value is None:
        return settings[default]
    if isinstance(value, str):
        return value
    if value < 16:
        return settings['palette'][value]
    if value >= 232:
        level = 8 + 10 * (value - 232)
        return '#%02x%02x%02x' % (level, level, level)
    number = value - 16
    levels = (0, 95, 135, 175, 215, 255)
    return '#%02x%02x%02x' % (levels[number // 36], levels[(number // 6) % 6], levels[number % 6])


class AnsiDecoder:
    def __init__(self):
        self.state, self.parameters = 'text', ''
        self.style = (None, None, False, False, False)
        self.cr = False

    def sgr(self, source):
        try:
            values = [int(value) if value else 0 for value in source.split(';')]
        except ValueError:
            return
        fg, bg, bold, underline, reverse = self.style
        index = 0
        while index < len(values):
            code = values[index]
            if code == 0: fg, bg, bold, underline, reverse = None, None, False, False, False
            elif code in (1, 22): bold = code == 1
            elif code in (4, 24): underline = code == 4
            elif code in (7, 27): reverse = code == 7
            elif 30 <= code <= 37: fg = code - 30
            elif 40 <= code <= 47: bg = code - 40
            elif 90 <= code <= 97: fg = code - 90 + 8
            elif 100 <= code <= 107: bg = code - 100 + 8
            elif code == 39: fg = None
            elif code == 49: bg = None
            elif code in (38, 48) and index + 1 < len(values):
                color = None
                if values[index + 1] == 5 and index + 2 < len(values):
                    if 0 <= values[index + 2] <= 255: color = values[index + 2]
                    index += 2
                elif values[index + 1] == 2 and index + 4 < len(values):
                    rgb = values[index + 2:index + 5]
                    if all(0 <= value <= 255 for value in rgb): color = '#%02x%02x%02x' % tuple(rgb)
                    index += 4
                if color is not None:
                    if code == 38: fg = color
                    else: bg = color
            index += 1
        self.style = fg, bg, bold, underline, reverse

    def feed(self, value, final=False):
        runs, text = [], []
        def flush():
            if text:
                runs.append((''.join(text), self.style)); text.clear()
        for char in value:
            if self.state == 'text':
                if self.cr:
                    text.append('\n'); self.cr = False
                    if char == '\n': continue
                if char == '\x1b':
                    flush(); self.state = 'escape'
                elif char == '\r': self.cr = True
                elif char in '\n\t' or ord(char) >= 32 and char != '\x7f': text.append(char)
            elif self.state == 'escape':
                self.parameters = ''
                self.state = 'csi' if char == '[' else 'string' if char in ']P^_X' else 'text'
            elif self.state == 'csi':
                if '@' <= char <= '~':
                    if char == 'm': self.sgr(self.parameters)
                    self.state = 'text'
                elif len(self.parameters) < 256: self.parameters += char
                else: self.state = 'text'
            elif self.state == 'string':
                if char == '\x07': self.state = 'text'
                elif char == '\x1b': self.state = 'string_escape'
            elif self.state == 'string_escape':
                self.state = 'text' if char == '\\' else 'string'
        if final and self.cr:
            text.append('\n'); self.cr = False
        flush()
        return runs
