#!/usr/bin/env python3
"""Build offline HTML documentation with Python's standard library only."""
import ast
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs'
PAGES = [
    ('index', 'Start here', 'Overview'), ('editor', 'Using the editor', 'Editor'),
    ('plugins', 'Plugin catalog', 'Plugins'), ('terminal', 'Project Terminal', 'Terminal'),
    ('ai', 'AI Chat & Code', 'AI'), ('custom-ai', 'Your own AI plugin', 'AI'),
    ('plugin-development', 'Plugin development', 'Developers'), ('build-your-ai', 'AI builder prompt', 'Developers'),
    ('php', 'PHP tools & examples', 'Languages'), ('reference', 'Shortcuts & troubleshooting', 'Reference'),
    ('team-work', 'Team Projects & Git', 'Collaboration'),
    ('handbook', 'Full project handbook', 'Reference')]
SOURCE_MAP = {'TEAM_WORK.md': 'team-work.html', 'README.md': 'handbook.html', 'PLUGIN_DEVELOPMENT.md': 'plugin-development.html',
              'examples/custom_ai/README.md': 'custom-ai.html', 'examples/custom_ai/BUILD_PROMPT.md': 'build-your-ai.html',
              'examples/php/README.md': 'php.html'}


def slug(value):
    return re.sub(r'[^\w-]+', '-', value.lower(), flags=re.UNICODE).strip('-') or 'section'


def local_link(value, source):
    parts = urlsplit(value)
    if parts.scheme:
        return value if parts.scheme in ('https', 'http', 'mailto') else None
    if value.startswith('#'):
        return value
    target = (source.parent / unquote(parts.path)).resolve()
    try: relative = target.relative_to(ROOT).as_posix()
    except ValueError: return None
    if target.parent == OUT and target.name in {name + '.html' for name, title, category in PAGES}:
        return target.name + ('#' + parts.fragment if parts.fragment else '')
    if relative in SOURCE_MAP:
        return SOURCE_MAP[relative] + ('#' + parts.fragment if parts.fragment else '')
    if target.exists():
        return '../' + quote(relative, safe='/') + ('#' + parts.fragment if parts.fragment else '')
    return None


def inline(value, source):
    # Tokenize before escaping; source markup never becomes executable HTML.
    pattern = r'`[^`\n]+`|!?\[[^\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*'
    output, cursor = [], 0
    for match in re.finditer(pattern, value):
        output.append(escape(value[cursor:match.start()]))
        token = match.group()
        if token.startswith('`'):
            output.append('<code>' + escape(token[1:-1]) + '</code>')
        elif token.startswith('**'):
            output.append('<strong>' + escape(token[2:-2]) + '</strong>')
        else:
            item = re.match(r'(!?)\[([^]]+)\]\(([^)]+)\)', token)
            image, label, target = item.groups()
            url = local_link(target, source)
            if url and image:
                output.append(f'<img loading="lazy" src="{escape(url, quote=True)}" alt="{escape(label, quote=True)}">')
            elif url:
                output.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
            else:
                output.append(f'<span title="Local reference is not bundled">{escape(label)}</span>')
        cursor = match.end()
    output.append(escape(value[cursor:]))
    return ''.join(output)


def markdown(text, source):
    lines, result, headings, used = text.splitlines(), [], [], {}
    index = 0
    def heading_id(label):
        base = slug(label); count = used.get(base, 0); used[base] = count + 1
        return base + (f'-{count}' if count else '')
    def block(line):
        return bool(re.match(r'^#{1,6} |^```|^\s*(?:[-*]|\d+\.) ', line))
    while index < len(lines):
        line = lines[index]
        if not line.strip(): index += 1; continue
        if line.startswith('```'):
            language = line[3:].strip(); code = []; index += 1
            while index < len(lines) and not lines[index].startswith('```'):
                code.append(lines[index]); index += 1
            result.append('<div class="code-block"><div class="code-top"><span>' + escape(language or 'text') +
                          '</span><button class="copy-code" type="button">Copy</button></div><pre><code>' + escape('\n'.join(code)) + '</code></pre></div>')
            index += 1; continue
        match = re.match(r'^(#{1,6}) (.+)$', line)
        if match:
            level, label = len(match[1]), match[2]
            identity = heading_id(label)
            result.append(f'<h{level} id="{identity}">{inline(label, source)}</h{level}>')
            if level in (2, 3): headings.append((level, label, identity))
            index += 1; continue
        if index + 1 < len(lines) and '|' in line and re.match(r'^\s*\|?\s*:?-{3,}', lines[index + 1]):
            rows = [line]; index += 2
            while index < len(lines) and '|' in lines[index] and lines[index].strip():
                rows.append(lines[index]); index += 1
            cells = lambda row: [cell.strip() for cell in row.strip().strip('|').split('|')]
            result.append('<div class="table-scroll"><table><thead><tr>' + ''.join('<th scope="col">' + inline(cell, source) + '</th>' for cell in cells(rows[0])) + '</tr></thead><tbody>')
            for row in rows[1:]: result.append('<tr>' + ''.join('<td>' + inline(cell, source) + '</td>' for cell in cells(row)) + '</tr>')
            result.append('</tbody></table></div>'); continue
        match = re.match(r'^\s*([-*]|\d+\.) (.*)$', line)
        if match:
            ordered = match[1][0].isdigit(); tag = 'ol' if ordered else 'ul'; result.append('<' + tag + '>')
            while index < len(lines):
                item = re.match(r'^\s*([-*]|\d+\.) (.*)$', lines[index])
                if not item or item[1][0].isdigit() != ordered: break
                content = [item[2]]; index += 1
                while index < len(lines) and lines[index].startswith('  ') and not block(lines[index]):
                    content.append(lines[index].strip()); index += 1
                result.append('<li>' + inline(' '.join(content), source) + '</li>')
            result.append('</' + tag + '>'); continue
        paragraph = [line]; index += 1
        while index < len(lines) and lines[index].strip() and not block(lines[index]):
            if index + 1 < len(lines) and '|' in lines[index] and re.match(r'^\s*\|?\s*:?-{3,}', lines[index + 1]): break
            paragraph.append(lines[index]); index += 1
        result.append('<p>' + inline(' '.join(paragraph), source) + '</p>')
    return '\n'.join(result), headings


def sections(text, names):
    parts = re.split(r'(?m)(?=^## )', text)
    return '\n\n'.join(part for part in parts if part.splitlines()[0].removeprefix('## ').strip() in names)


DESCRIPTIONS = {
    'ai_assistant': 'Bundled free-model AI Chat and AI Code, explicit attachments, Ask/Team workflows, history, reply caps, and provider cooldowns. Uses the packaged tools/ai scripts, with a legacy sibling fallback.',
    'code_theme': 'Dark, Light, Dracula, and Solarized Dark document themes with bounded syntax highlighting. Theme choices do not replace editor chrome.',
    'editor_commands': 'Fourteen everyday line-editing, tab-navigation, clipboard-path, and zoom commands. Line changes support Undo; tab closing checks unsaved edits.',
    'my_ai_example': 'Independent customizable AI tool with Responses/Chat Completions, provider settings, model discovery, explicit file attachments, and local request limits.',
    'php_completion': 'PHP completion through the local Intelephense language server, with snippets, project/library configuration, and stale-result protection.',
    'php_formatter': 'Prettier PHP formatting of the active buffer with one-step Undo; does not save the document to disk.',
    'plugin_builder': 'Preview and create a simple command, text transform, tool window, or event/service plugin. Exclusive creation protects existing files.',
    'project_explorer': 'Project tree with Open/Save/Load project controls, file operations, and launchers for independent Git and hosted teamwork.',
    'git_project': 'Independent Git tools for any project: automatic init, shared repository service, local Git settings, clone, origin, status, commits, pull, and push. Optional Explorer integration.',
    'team_project': 'Host/client project copies, individual keys, portable client invitations, modern group chat, and audio/video/file exchange.',
    'project_terminal': 'Output-only system-shell runner with stop controls, per-project commands, ANSI rendering, semantic categories, and portable appearance preferences.',
    'python_formatter': 'Python formatting using optional Black, with settings and Undo. Black must be installed in the editor’s interpreter.',
    'rich_text': 'Text styles and formatted .pocket copies. Plain-text Save does not preserve formatting. Text Undo is available; formatting Undo is not implemented.',
    'search_replace': 'Find and replace with previous/next navigation, regex options, and bounded regex execution in a disposable process.',
    'syntax_diagnostics': 'Non-executing Python/JSON syntax checks in a worker, error markers, jump-to-error, and a status/check service.',
    'text_formatting': 'Preview transformations for selection or document: case changes, whitespace cleanup, indentation, sorting, and duplicate-line removal.',
    'word_count': 'Counts words in the selected document and displays the result.',
}


USAGE = {
    'git_project': 'Open Git Project or use Explorer Actions. Browse selects a folder and initializes Git if absent; Set Git Folder prepares a typed path. The repository root is available through ensure_repository/get_repository services. Clone downloads a repository. Set Origin configures its remote. Save files, Refresh, select changes, and Commit Selected. Push publishes; Pull only fast-forwards and requires closed project tabs. Settings edits repository-local author identity, origin, line endings, executable-bit tracking, and commit signing; blank/Inherit uses inherited defaults.',
    'team_project': 'Choose Host a project or Join as client. Host mode shows its actual listen port and suggests a LAN address. Easy Internet Setup detects OS/CPU, installs a checksum-verified user-local cloudflared, and starts a temporary HTTPS relay. Only a verified relay URL is filled into invitations. It also exports setup commands/scripts. Client mode loads an invitation; no client listening port is needed. Details shows local and peer endpoints; the host client list shows last observed IPs/source ports. Under Project Copies, generate a named client key and export a .team-invite using a reachable server URL. Detect My Public IP is at the top for both roles before connecting and uses HTTPS fallback services with visible DNS/network errors; Use HTTPS IP sets the invitation address only after an explicit click. HTTPS and actual inbound reachability must be configured separately. The client loads that invitation, chooses a local copy folder, and clicks Join Team. Use Group Chat and Shared Files to communicate, and Project Copies to pull or send saved file changes.',
    'code_theme': 'Open Code Theme, choose a color scheme and language, and close the dialog. Auto selects highlighting from the filename extension. Highlighting stops above 300,000 characters; text remains editable.',
    'editor_commands': 'Place the cursor on a line or select several lines, then invoke a line command from the palette. Selection ending at the next line’s column zero excludes that next line. Go to Line asks for a valid line number. Copy Path needs a saved filename. Zoom preferences persist.',
    'project_explorer': 'Choose Open Project and select a folder. Expand the tree and double-click a file, or press Enter. Use Actions or the context menu for New File, New Folder, Rename, Delete, and Refresh. Rename is also available through F2. File operations require explicit UI actions and retain unsaved-file protection.',
    'python_formatter': 'Open a Python document and choose Format Python. Review the buffer, use Undo if needed, and Save separately. Python Format Settings selects a line length from 40 to 200. Black can also use project configuration and fmt: off regions. A missing Black dependency produces an error rather than silently changing tools.',
    'rich_text': 'Select text before applying Bold, Italic, Underline, Strikeout, font, size, text color, or highlighting. Paragraph alignment affects the relevant paragraphs. Clear Formatting removes rich tags from the selection. Save Formatted Copy creates a .pocket file; Open Formatted loads one into a new tab. Formatting changes require a formatted copy to preserve them.',
    'search_replace': 'Open Search & Replace, enter the search and optional replacement, choose case/regex options, then use Next or Previous. Replace selects the next match before replacing if no matching active result is selected; Replace All works on the active document. Regex replacements support capture references. Invalid expressions and bounded-search failures are shown without replacing the document.',
    'text_formatting': 'Select a region or leave no selection to target the whole document. Open Text Formatting, choose an operation and indentation width, and review the preview. Apply Formatting performs an undoable change. If the document or selected range changed while the dialog was open, reopen the formatter instead of applying stale results.',
    'word_count': 'Choose Count words to count whitespace-separated words in the whole active document, including unsaved edits. A text selection does not restrict this count.',
    'syntax_diagnostics': 'Save or open a Python/JSON filename, then edit. After the debounce pause, the first syntax error is underlined and its message appears below the editor. Click the message or invoke Go to Syntax Error. Check Syntax runs manually; Toggle Automatic Syntax Checks affects this session. Checks skip buffers over 200,000 characters and never execute source.',
    'plugin_builder': 'Choose a unique lowercase plugin ID, command label, description, and template. Inspect Code preview and Documentation, then Create & load. A source tab opens for editing. Restart after changing an already-loaded plugin. The Plugin communication page lists loaded IDs/services; Refresh services updates it.',
    'ai_assistant': 'Configure provider credentials through /keys in tools/ai/chat_ai.py or tools/ai/code_ai.py, open AI Chat or AI Code, and Refresh models. Select Ask or Team, explicitly attach files/current document, then Send. Stop cancels the local process. Use Reply tokens only when you intend to raise the requested cap; provider limits still apply.',
    'my_ai_example': 'Open My AI Example and click Settings. Select a preset/API format, enter an API key if required, refresh models or type an exact model ID, and set limits. Use + Files for up to four UTF-8 snapshots and the active-tab checkbox for unsaved editor context. Enter sends, Shift+Enter adds a line, and Open reply creates a new unsaved document.',
    'project_terminal': 'Set a working directory and command, then Run. Stop terminates the process group where supported. Save Project Command remembers the command for that directory. Settings configures presets, ANSI colors, categories, font/encoding, and force-color. This runner has no interactive input.',
    'php_formatter': 'Open a PHP-family document, invoke Format PHP, review the buffer, and Save separately. Use PHP Format Settings for print width and target version. Missing local Node dependencies or invalid syntax leave the text unchanged. Undo reverts a formatting change.',
    'php_completion': 'Choose Open Project, open a PHP-family file, and press Ctrl+Space or type an identifier. Navigate suggestions with arrows, accept with Enter/Tab, and dismiss with Escape. Settings controls automatic delay, limits, PHP version, and additional library folders. Use Reindex PHP Project after external filesystem changes.',
}


def plugin_metadata(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    values = {name: [] for name in ('add_command', 'provide_service', 'on', 'emit')}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in values and node.args:
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                values[node.func.attr].append(node.args[0].value)
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
            registers = any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == 'add_command' for child in ast.walk(node))
            if registers:
                for item in node.iter.elts:
                    if isinstance(item, ast.Tuple) and item.elts and isinstance(item.elts[0], ast.Constant) and isinstance(item.elts[0].value, str):
                        values['add_command'].append(item.elts[0].value)
    return {key: list(dict.fromkeys(items)) for key, items in values.items()}


def build():
    OUT.mkdir(exist_ok=True)
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    editor_sections = ['Launch', 'Appearance and preferences', 'Documents and settings', 'Command palette', 'Quick Open', 'Session restore and draft recovery', 'Split editor', 'About and tab appearance']
    terminal_sections = ['Project Terminal colors and portable defaults', 'Terminal output categories']
    content = {
        'index': ('# Pocket Editor documentation\n\nA complete local guide to the editor, its bundled plugins, and building your own tools. Created by **Somi Douth** · [somidouth@gmail.com](mailto:somidouth@gmail.com).\n\n## Start in three steps\n\n1. Launch `python3 -B pocket_editor.py` from the project directory.\n2. Open files or a project, then use Ctrl+P for file search and Ctrl+Shift+P for commands.\n3. Explore Extensions for plugins, and Help for read-only author and contact information.\n\n## Choose your guide\n\n' + '\n'.join(f'- [{title}]({name}.html) — {category}' for name, title, category in PAGES[1:]) + '\n\n## Offline by design\n\nOpen this HTML file in a browser. No server, account, CDN, JavaScript framework, analytics, or network request is needed to read the documentation. Search works locally when JavaScript is enabled. External reference links require internet access.\n\n## Verification scope\n\nThe editor and GUI tests were exercised on Linux. Windows/macOS runtime behavior remains unverified. AI integration tests use offline transports; they do not prove provider availability, account access, pricing, or live response quality.\n', ROOT / 'README.md'),
        'editor': ('# Using Pocket Editor\n\n' + sections(readme, editor_sections), ROOT / 'README.md'),
        'terminal': ('# Project Terminal\n\nRun commands from a chosen directory using the operating system’s shell. This is an output-only runner: interactive shells, password prompts, full-screen terminal programs, and input forwarding are unsupported. Use Run, Stop, Save Project Command, and Settings. The default directory comes from the project, then the active file, then the current working directory.\n\n' + sections(readme, terminal_sections), ROOT / 'README.md'),
        'ai': ('# AI Chat and Code\n\nThis is the bundled free-model frontend, separate from My AI Example. It uses the packaged `tools/ai/chat_ai.py` and `tools/ai/code_ai.py` backends. Provider credentials are supplied separately by the user.\n\n' + sections(readme, ['AI Chat and Code']) + '\n\n## Full reply display\n\nThe current assistant/team reply is preserved while older transcript content is trimmed toward 200,000 characters. Reply tokens → Auto retains the conservative defaults. Choose an explicit cap up to 8,192 when a longer reply is needed; this can increase usage. A larger cap does not override provider quotas or timeouts. No automatic continuation request is made.\n', ROOT / 'README.md'),
        'custom-ai': ((ROOT / 'examples/custom_ai/README.md').read_text(encoding='utf-8'), ROOT / 'examples/custom_ai/README.md'),
        'plugin-development': ((ROOT / 'PLUGIN_DEVELOPMENT.md').read_text(encoding='utf-8'), ROOT / 'PLUGIN_DEVELOPMENT.md'),
        'build-your-ai': ('# Build a custom AI plugin with AI Code\n\nCopy this prompt after attaching the relevant example source and PluginAPI documentation. Never attach real credentials.\n\n' + (ROOT / 'examples/custom_ai/BUILD_PROMPT.md').read_text(encoding='utf-8'), ROOT / 'examples/custom_ai/BUILD_PROMPT.md'),
        'php': ('# PHP tools and completion exercises\n\n' + sections(readme, ['PHP formatting and completion']) + '\n\n' + (ROOT / 'examples/php/README.md').read_text(encoding='utf-8').replace('# PHP test project', '## PHP example project', 1), ROOT / 'README.md'),
        'team-work': ((ROOT / 'TEAM_WORK.md').read_text(encoding='utf-8'), ROOT / 'TEAM_WORK.md'),
        'handbook': (readme, ROOT / 'README.md'),
    }
    plugin_paths = sorted(path for path in (ROOT / 'pocket_editor_plugins').glob('*.py') if not path.name.startswith('_'))
    catalog = '# Bundled plugin catalog\n\nEvery loadable plugin in the source tree is listed below. Helpers beginning with `_` are not standalone plugins. Commands and service/event names are extracted from the Python syntax tree without executing plugins. Event lists show statically declared names, not every dynamic event.\n\n' + sections(readme, ['Plugins', 'Plugin Manager', 'Plugin Builder', 'Inline syntax diagnostics'])
    for path in plugin_paths:
        metadata = plugin_metadata(path)
        catalog += f'\n\n## {path.stem}\n\n{DESCRIPTIONS.get(path.stem, ast.get_docstring(ast.parse(path.read_text())) or "Plugin.")}\n\n'
        catalog += '**How to use:** ' + USAGE.get(path.stem, 'Run its commands from Extensions or the palette.') + '\n\n'
        catalog += 'Commands: ' + ', '.join('`' + label + '`' for label in metadata['add_command']) + '.\n\n'
        for field, label in [('provide_service', 'Services'), ('on', 'Subscribed events'), ('emit', 'Published events')]:
            if metadata[field]: catalog += label + ': ' + ', '.join('`' + (path.stem + '.' if field == 'provide_service' else '') + name + '`' for name in metadata[field]) + '.\n\n'
        catalog += f'[View source](pocket_editor_plugins/{path.name})\n'
    content['plugins'] = catalog, ROOT / 'README.md'
    content['reference'] = '''# Shortcuts, settings, and troubleshooting

## Keyboard shortcuts

On macOS, Cmd replaces Ctrl for the editor shortcuts below. Plugin-specific shortcuts retain their documented behavior.

| Shortcut | Action |
| --- | --- |
| Ctrl+N / Ctrl+O | New document / open files |
| Ctrl+S / Ctrl+Shift+S | Save / Save As |
| Ctrl+Shift+A | Save All |
| Ctrl+W / Ctrl+Q | Close file / quit |
| Ctrl+P | Quick Open |
| Ctrl+Shift+P | Command palette |
| Ctrl+F / Ctrl+G | Find / Find Next |
| Ctrl+Space | PHP completion in PHP documents |
| Enter / Shift+Enter | Send / new line in AI composers |

Undo, Redo, Cut, Copy, Paste, and Select All are also available in Edit. Split commands are under View; they have no dedicated default shortcut.

## Command-line launch

```sh
python3 -B pocket_editor.py
python3 -B pocket_editor.py path/to/file.py
python3 -B pocket_editor.py --safe-mode
python3 -B pocket_editor.py --no-session
```

Safe mode prevents plugin execution while retaining Plugin Manager through Settings. No-session skips both restoring and writing recovery snapshots.

## Settings and local files

| File | Purpose |
| --- | --- |
| pocket_editor_settings.json | Font, wrapping, line numbers, tab width, toolbar selection, disabled plugins |
| editor_about.py | Published read-only author/contact details |
| pocket_editor_session.json | Session tabs, cursor/scroll positions, unsaved text/formatting snapshots |
| pocket_editor_session.lock | Session ownership lock; another editor instance avoids overwriting recovery data |
| pocket_editor_session.invalid.json | Preserved malformed recovery snapshot |
| pocket_editor_plugins/project_terminal_settings.json | Saved project commands and global _appearance preferences |
| pocket_editor_plugins/my_ai_example_settings.json | Custom AI provider/model/API format/reasoning/limits; excludes API keys |
| pocket_editor_plugins/code_theme_settings.json | Document theme and language selection |
| pocket_editor_plugins/php_completion_settings.json | PHP completion project/library preferences |
| pocket_editor_plugins/php_formatter_settings.json | PHP formatting preferences, when saved |
| pocket_editor_plugins/python_formatter_settings.json | Optional Black formatting preferences |

Plugin setting files are created when needed. Recovery snapshots contain local document content. Custom AI keys come from its memory-only field or environment variables, not from its settings file. The bundled AI backend manages its credentials separately; consult its `/keys` setup.

## Troubleshooting

- **A plugin does not appear:** use Load New Plugins, check Plugin Manager for errors/disabled status, and restart after editing a loaded plugin.
- **A plugin breaks startup:** launch with --safe-mode, disable it through Settings → Plugin Manager, and restart normally.
- **Text is unsaved:** a dot/amber title marks edits. Closing prompts Save, Discard, or Cancel. Save As cancellation and failed writes keep the file open.
- **Formatting vanished after saving text:** use Rich Text → Save Formatted Copy for .pocket output. Plain-text Save cannot retain formatting.
- **Terminal text is uncolored:** select a preset, enable ANSI or category coloring, or explicitly request force-color if your tool suppresses ANSI for piped output. Check encoding for legacy Windows tools.
- **An AI reply ends early:** inspect the reply cap, provider errors, and timeout. Explicitly choose a larger cap if appropriate; this can use more credits.
- **OpenAI rejects authentication:** use a Platform API key in My AI Example, not a Codex subscription login token. No credentials are read from Codex login files.
- **Provider returns 429:** wait for the local pause, then check provider rate limits and available quota; retries cannot reset the account limit.
- **Attachments exceed the input budget:** remove files/history or raise the limit explicitly. PDFs/images are not supported by the custom example.
- **PHP completion is missing:** confirm the file type, local Node tools, project folder, target PHP version, and library folders; reindex after external changes.
- **Python formatting is unavailable:** install Black in the interpreter running Pocket Editor. It is optional; do not change the editor’s Python environment accidentally.

## Verification and scope

Use the tests relevant to your changes. They require a graphical desktop for real Tk checks.

```sh
python3 -B -m unittest -v test_editor
python3 -B -m unittest -v test_plugin_builder test_plugin_manager
python3 -B -m unittest -v test_my_ai_example test_ai_assistant
python3 -B -m unittest -v test_terminal_colors test_split_editor
python3 -B -m unittest -v test_quick_open test_command_palette test_editor_session
python3 -B -m unittest -v test_syntax_diagnostics test_tabs_about test_php
python3 -B tools/documentation/build_docs.py
python3 -B -m unittest -v test_documentation
```

The terminal and process integration tests were run on Linux; Windows/macOS runtime behavior is not verified. Mocked AI tests make no real provider requests. No project/framework license is assigned by this documentation.

## About

Pocket Editor by **Somi Douth**. Contact [somidouth@gmail.com](mailto:somidouth@gmail.com). Built with Python and Tkinter. Help → About Pocket Editor displays the published author and contact information; it has no editing control.
''', ROOT / 'README.md'
    search = []
    for name, title, category in PAGES:
        text, source = content[name]
        # Local navigation in the landing page is authored relative to docs itself.
        if name == 'index':
            source = OUT / 'index.md'
        body, headings = markdown(text, source)
        navigation = ''.join(f'<a href="{identity}.html"' + (' aria-current="page"' if identity == name else '') + f'><span>{escape(label)}</span><small>{escape(group)}</small></a>' for identity, label, group in PAGES)
        toc = ''.join(f'<a class="level-{level}" href="#{identity}">{escape(label)}</a>' for level, label, identity in headings)
        page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark light"><meta name="description" content="{escape(title)} — Pocket Editor documentation by Somi Douth"><title>{escape(title)} · Pocket Editor Docs</title><link rel="stylesheet" href="assets/docs.css"><script src="assets/search-index.js" defer></script><script src="assets/docs.js" defer></script></head>
<body><a class="skip" href="#content">Skip to content</a><header class="topbar"><a class="brand" href="index.html"><b class="brand-mark">P</b><span>Pocket Editor <small>DOCUMENTATION</small></span></a><div class="search-wrap"><label class="sr-only" for="doc-search">Search documentation</label><input id="doc-search" type="search" placeholder="Search documentation…" autocomplete="off" aria-controls="search-results" aria-expanded="false"><div id="search-results" hidden></div><span class="sr-only" id="search-status" aria-live="polite"></span></div><button id="theme-toggle" type="button" aria-label="Switch color theme">Theme</button><button id="nav-toggle" type="button" aria-expanded="false" aria-controls="sidebar">Menu</button></header><div class="layout"><aside id="sidebar"><p class="nav-label">THE GUIDE</p><nav aria-label="Documentation">{navigation}</nav><div class="author"><strong>Somi Douth</strong><a href="mailto:somidouth@gmail.com">somidouth@gmail.com</a><span>Local docs · no account required</span></div></aside><main id="content" tabindex="-1"><div class="eyebrow">{escape(category)} / POCKET EDITOR</div><article>{body}</article><footer>Created by Somi Douth · Built with Python and Tkinter <button class="print-doc" type="button">Print / save PDF</button></footer></main><aside class="toc"><p class="nav-label">ON THIS PAGE</p><nav aria-label="On this page">{toc or '<a href="#content">Overview</a>'}</nav></aside></div></body></html>'''
        (OUT / (name + '.html')).write_text(page, encoding='utf-8')
        # Section excerpts are inlined as JS so search also works under file://.
        blocks = re.split(r'(?m)(?=^## )', text)
        for block in blocks:
            first = block.splitlines()[0] if block.splitlines() else title
            heading = first.removeprefix('## ').removeprefix('# ').strip()
            url = name + '.html' + ('#' + slug(heading) if first.startswith('## ') else '')
            search.append({'title': heading, 'page': title, 'url': url, 'text': block[:14000]})
    assets = OUT / 'assets'; assets.mkdir(exist_ok=True)
    (assets / 'search-index.js').write_text('window.POCKET_DOCS=' + json.dumps(search, ensure_ascii=False).replace('<', '\\u003c') + ';\n', encoding='utf-8')
    print(f'Built {len(PAGES)} HTML pages; catalog covers {len(plugin_paths)} plugins; {len(search)} searchable sections.')


if __name__ == '__main__':
    build()
