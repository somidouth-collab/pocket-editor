# Pocket Editor

Created by **Somi Douth** · [somidouth@gmail.com](mailto:somidouth@gmail.com)

A portable Python 3.9+ / Tkinter editor. The core editor needs only Tkinter. Python formatting optionally uses Black. The PHP plugins use a separate local Node.js tool installation (Prettier PHP and Intelephense).

## Launch

```sh
cd pocket-editor
python3 -B pocket_editor.py
```

Open a file with `python3 -B pocket_editor.py path/to/file.py`. Start without plugins with `python3 -B pocket_editor.py --safe-mode`.

On Windows use `py pocket_editor.py`; on macOS use `python3 pocket_editor.py` with a Python installation that includes Tkinter. These platforms have not yet been verified. Debian requires Tkinter (`python3-tk`). Black must be available in the interpreter running the editor; it was available on the development computer during verification.

## Appearance and preferences

The interface uses a consistent charcoal palette, a compact New/Open/Save toolbar, a compact Extensions dropdown and optional toolbar shortcuts, wrapping file tabs with close buttons and unsaved markers, and a detailed status bar. Settings → Preferences changes document font, tabs, line numbers, and wrapping. Document font changes do not alter UI or gutter fonts. Code Theme controls the document color scheme independently of the UI palette.

Extensions → Add to toolbar lets you check only the commands you want as shortcuts. The toolbar starts empty, saves your selections, and stays on one row. Shortcuts that do not fit the window are available in More; all plugin commands remain in Extensions.

## Documents and settings

Open selects one or more files. Each file has its own tab, edits, undo history, cursor, and formatting. Opening the same file again selects its existing tab. New creates another untitled tab. Click × or press Ctrl+W (Cmd+W on macOS) to close the current file; modified files offer Save, Discard, or Cancel. Canceling Save As or a failed save keeps the file open. Save writes the selected file; File → Save All saves edited and untitled files. Quitting checks every open document. Plain-text saving still requires a formatted copy to preserve rich-text styles.

Plain UTF-8 saves retain the detected BOM and newline convention. Mixed newline conventions are normalized on opening. Saves and settings use a temporary sibling file and atomic replacement; existing symlinks are followed.

Use Rich Text → Save Formatted Copy for `.pocket` documents. File → Save writes plain text. Unsaved formatting is protected by a separate discard confirmation. Rich formatting operations do not have formatting undo; text changes have Tk undo/redo. Word `.docx` support is not implemented.

## Plugins

Python files in `pocket_editor_plugins/` define `register(api)`. Plugin IDs are filename stems. Plugins load in filename order and repeated Load New Plugins does not register them twice.

The API supports commands, text access/replacement, paths, events, services, and discovery. `api.list_services()` returns qualified service names. `api.on_cleanup(callback)` registers cleanup for failed registration or editor exit. Event callbacks and service calls execute on the GUI thread. Event payload key `source` is reserved. Plugins are trusted Python code; plugins that access private editor internals must register cleanup for their own side effects.

The terminal is an output-only shell runner. Its output queue and retained text are bounded; retained text is limited to 200,000 characters and individual long lines retain their last 8,192 characters. Stop and application exit terminate the process group on POSIX. Deliberately detached processes are outside this guarantee. Windows process-tree handling needs platform verification.

Regex searches run in a disposable Python process with a 0.5-second execution timeout and a 100,000-match limit. A search may briefly pause the GUI while that bounded operation completes. Syntax highlighting pauses above 300,000 document characters.

## Verification

From the project directory, on a graphical desktop:

```sh
python3 -B -m unittest -v test_editor
```

See [release notes](RELEASE_NOTES.md) for the release scope and [the GUI preview](docs/assets/previews/gui-preview.png) for the updated appearance.

## PHP formatting and completion

Two independent plugins are available in Extensions:

- **Format PHP** formats the current PHP buffer in a worker, with cursor preservation and one-step Undo. It does not write to disk. Invalid PHP, tool failure, a 30-second timeout, or a changed/switched document leaves the active text intact.
- **PHP Format Settings** selects line width and target PHP version. Editor tab width supplies the default indentation; project Prettier and EditorConfig settings override defaults.
- **PHP Completion** (Ctrl+Space) requests suggestions. Completion also appears automatically while typing PHP identifiers, `->`, or `::`. Use arrows to select, Enter/Tab or a click to insert, and Escape to dismiss. Other file types retain their normal editing keys.
- **PHP Completion Settings** controls automatic suggestions, typing delay (default 120 ms), minimum characters, result limit, project PHP version and extra library/framework-stub folders. Settings are validated and saved atomically per editor/project.
- **PHP Completion Help** shows usage, workspace, worker phase, response timing and the last error. **Reindex PHP Project** rebuilds the workspace after external file changes.

Completion uses Intelephense's free completion features through LSP, not a regex symbol guesser. It resolves namespaces, class imports, aliases, grouped `use`, imported functions, Composer PSR-4 classes and included files present in the workspace. Standard LSP additional edits support inserted `use` declarations. The workspace comes from a containing Explorer project, then a Composer/git ancestor, then the file folder. Saved PHP files notify the server; Explorer filesystem operations trigger reindexing on the next request. The first request may take several seconds to index. PHP buffers above 2 MB are excluded. Numeric snippets, default parameter placeholders and choices are supported; Tab/Shift+Tab navigates parameters. Mirrored placeholders, snippet variables and transforms are rejected safely instead of modifying the document incorrectly.

The plugins activate only for PHP-family extensions, including `.php` and `.phtml`. No PHP interpreter or Composer executable is needed for formatting/completion. Executing PHP still requires PHP; no PHP runtime was available during this audit. Dependencies missing from a PHP application's vendor folder must be installed separately by that application's developer.

The tool packages are pinned in `tools/php/package.json` and `package-lock.json`. They are already installed on this development computer. To restore them after copying the editor to another computer, install Node.js and npm, then run from the editor directory:

```sh
npm ci --prefix tools/php --ignore-scripts
```

Node.js 22+ is recommended because some transitive dependencies require it. The tested PHP features passed here with the existing Node.js 20.19.2, which produced engine warnings for those dependencies during installation. No system packages were installed. PHP dependencies are optional, occupy about 172 MB locally, and are separate from the lightweight editor core. `node_modules` is excluded from source control. Intelephense is proprietary software with free completion features; this integration does not use premium features or supply a licence key. Keep its licence with any permitted local installation.

Sources: [Prettier PHP](https://github.com/prettier/plugin-php), [Prettier API](https://prettier.io/docs/api), [Intelephense documentation and licence information](https://intelephense.com/docs), [Language Server Protocol](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/).

### PHP test document

```sh
python3 -B pocket_editor.py examples/php/demo.php
```

See `examples/php/README.md` for completion exercises. Run both suites on a graphical desktop:

```sh
python3 -B -m unittest -v test_editor test_php
```

The latest combined suite passed 29 tests on Debian. Windows/macOS UI, process handling, and tool installation still require platform verification.

### Refined completion behavior

The PHP worker cancels obsolete requests, keeps only the latest waiting task, warms analysis after opening PHP files, and maintains a short-lived bounded exact-result cache. The UI checks document revision and cursor identity rather than copying the full document repeatedly while polling. Suggestions are ranked using prefix/case and LSP ordering. The popup provides scrolling, symbol kind/type, lazily resolved documentation when supported, response timing, and keyboard/page navigation. Escape cancels delayed/in-flight results as well as hiding the popup, so an older request cannot reopen it.

Project indexes live under `tools/php/cache/`, excluded from source control. Concurrent sessions use a temporary workspace if the persistent cache is already locked. POSIX stale PID locks are recovered; read-only cache locations also fall back to temporary storage. No user source files are written by completion. Existing document font and toolbar shortcut preferences are preserved.

For your PHP code: open its folder with **Extensions → Open Project**, open a PHP document, then use **PHP Completion Settings → Add folder…** for libraries outside the project or framework stubs. Select the project's PHP version there. Composer dependencies must already be present. `use`, aliases, inheritance, traits, interfaces, enums, typed return values, PHPDoc array element types, PHPDoc magic methods, built-in functions and external library paths are covered by real-server tests. Highly dynamic `eval`, runtime-generated members, magic without annotations and framework-specific templates are not guaranteed; provide PHPDoc/stubs where needed. This is not a claim of complete support for every PHP program or framework.

`examples/php/advanced_completion.php` adds advanced exercises. `audit/php-completion-performance.json` records the measured demo timings: first worker request 1,178.42 ms (a persisted index may be reused), five warm requests median 14.72 ms (range 4.74–155.55 ms), and ten exact-cache repeats median 0.17 ms. These worker measurements exclude the configurable typing debounce and Tk rendering and are not a performance guarantee for large projects. Only the bundled PHP fixtures and temporary external-library projects were available for validation; no separate user application was supplied.

## AI Chat and Code

The AI window matches the editor’s charcoal theme, blue accents, compact controls, and UI font family, with Chat/Code navigation and a bottom
composer, styled user/assistant cards, lightweight Markdown and code blocks,
removable attachment chips, and a welcome screen with draft starters. Enter sends;
Shift+Enter inserts a newline. The composer clears only after a request starts;
a failed start preserves the draft. AI fonts/styles are isolated from editor
settings. The sidebar collapses in narrow windows; More keeps Copy, Reset,
Refresh models and Help accessible. Reset context clears model history while
keeping displayed messages available to copy. Copy preserves original Markdown
and code text even when formatting markers are hidden in the chat display.

[Conversation preview](docs/assets/previews/ai-chat-preview.png) ·
[Welcome preview](docs/assets/previews/ai-chat-welcome.png) ·
[Narrow-window preview](docs/assets/previews/ai-chat-narrow.png).
These previews use offline example messages; no provider request was sent.


Open **Extensions → AI Chat…** or **AI Code…**. The window has Chat/Code and
Ask/Team selectors. Ask requires one model and a question. Team shows a lead
selector and worker list; Ctrl-click workers from different developers, then
enter the task. Workers run in displayed order. Refresh models reloads available
models. Requests run in a separate process; Cancel stops that process.

Attach current document sends a snapshot including unsaved edits. Attach files
adds UTF-8 files. Attachments remain until cleared; no document is sent implicitly.
Replies appear in the output pane and never overwrite a document. Copy conversation
copies the retained visible prompts, replies and team output, excluding hidden
attachment text. Solo histories are separate per mode/model; the latest six turns
are stored during the session, and Reset conversations clears them. Requests send
only the recent complete turns that fit the active fast profile. Omitted history
is noted in the transcript; earlier visible conversation remains copyable. Team
runs are independent.

The plugin uses these conservative **fast presets**, rather than claiming a
universal optimum for every model:

| Limit | Chat | Code |
| --- | --- | --- |
| Task + attachments, UTF-8 | 4 KiB | 12 KiB Ask / 6 KiB Team |
| Requested reply cap | 512 tokens | 1,536 tokens |
| Outgoing history | Up to 2 turns / 4 KiB | Up to 3 turns / 8 KiB |
| Attachments | 2 | 4 |
| Team workers, excluding lead | 2 | 2 |
| Ask timeout | 60 seconds | 120 seconds |
| Team timeout | 120 seconds | 180 seconds |

The active limits and input usage are shown in the AI window. Input byte counts
include prompt, file headers and separators. Oversized tasks/files are rejected
with an explanation, preserving the prompt and previous attachments. Large files
are read only up to a bounded size. A team request whose source plus Python syntax
check metadata exceeds the backend source budget is rejected before model calls.
Existing provider-size retry behavior may shorten team context with a visible
backend notice. Reply caps can leave large answers incomplete; Code asks for
focused patches rather than full-file rewrites. Timeouts stop the local request
process and keep displayed partial output. Model discovery has a 45-second limit.
The shared command-line scripts are not changed; the GUI bridge supplies reply
caps at runtime to solo, planning, worker and final-answer calls.

Ask makes fewer model calls than Team. These presets target smaller requests and
shorter replies; actual latency depends on the model/provider. No live-provider
speed benchmark was performed. General rationale: [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization).

The plugin uses `tools/ai/chat_ai.py` and `tools/ai/code_ai.py` and their shared
credentials. Configure keys and Free plan confirmations via `/keys` in either
script before use. Launch the editor again, or select **Load New Plugins**.
No API request is made until opening the assistant (model discovery) or Send.
Run offline coverage with `python3 -B -m unittest -v test_ai_assistant` on a desktop.

### AI capabilities and provider failures

The AI backend now receives explicit instructions that Pocket Editor supplies only
prompt/history/attached text. It has no tools to browse a project, read files on
its own, write files, inspect the machine, run commands/tests, or commit changes.
It may explain attachments and propose patches for the user to apply. Any model
claim of direct local access is incorrect; model-generated claims do not grant
capabilities. Earlier misleading replies are not proof of tool access.

The normal fast reply caps remain 512 Chat / 1,536 Code tokens. If a model returns
an empty visible reply, the plugin retries that same call **once**, with up to
2,048 Chat / 3,072 Code output/reasoning tokens. A notice appears before the retry.
This can help reasoning models that exhaust a small output budget, but does not
guarantee success and may use an extra request/quota. No hidden reasoning is
shown as the final answer. A second empty result produces a readable error.

HTTP 429 is not automatically retried. The UI displays a provider rate-limit
message, preserves the draft when sending is blocked, and pauses new requests
using that provider for its Retry-After interval when available (at most one day)
or 30 seconds otherwise. The pause is local and cannot reset a provider's quota;
actual free-tier/shared-provider limits can last longer. Select a different
provider or wait. Provider messages do not expose raw Python tracebacks. Failed
team planning/finalization is marked as failed even when the command-line backend
catches the exception; worker-only failures allow a partial result with an
explicit status. A successful size-limit retry does not leave a stale failure.

Replies and team logs use structured text events, so answer lines that resemble
protocol markers cannot be mistaken for model lists or history records. Normal
fast limits, Copy, the chat theme and Stop remain in place. Shared backend scripts
were not modified.

Sources: [OpenRouter reasoning/output budgets](https://github.com/OpenRouterTeam/docs/blob/main/guides/best-practices/reasoning-tokens.mdx),
[OpenRouter error handling](https://github.com/OpenRouterTeam/docs/blob/main/api_reference/errors-and-debugging.mdx).
Verification used offline transports, not live provider requests; provider quota
recovery and the reported models' current availability remain unverified.

## Plugin Builder

Use **Extensions → Plugin Builder…** to preview, create, and load a command, text transform, tool window, or event/service plugin. Existing plugin files are protected from overwriting. See [Plugin development and API reference](PLUGIN_DEVELOPMENT.md) for the tutorial, templates, events, services, cleanup, testing, and reload workflow.

## Command palette

Press **Ctrl+Shift+P** (**Cmd+Shift+P** on macOS), or choose **Edit → Command Palette…**. Type words from a command name or menu category, use ↑/↓ to select, and press Enter to run. Escape closes without making changes. Plugin commands and toolbar toggles appear automatically from the live menus; reopen the palette after loading new plugins. Disabled commands are excluded, and the palette works in safe mode.

The bundled **editor_commands** plugin adds 14 everyday commands: Duplicate Line / Selection, Move Lines Up/Down, Delete Lines, Trim Trailing Whitespace, Go to Line, Copy File/Folder Path, Next/Previous Tab, Close Other Tabs, and Zoom In/Out/Reset. Line commands act on selected lines or the cursor's line and support Undo. Close Other Tabs checks unsaved changes and stops on cancellation or save failure. Zoom changes persist in editor settings. Load New Plugins or restart to enable newly installed commands; they are available from Extensions and the palette.

## Session restore and draft recovery

Normal application launch restores saved file tabs, the active tab, cursor positions, and vertical scroll positions. While the editor is running, a local snapshot is written every five seconds to `pocket_editor_session.json` beside its settings file. After an interrupted session, a prompt offers to restore unsaved text and rich-text styles; original files remain unchanged until you save. Edits made since the last snapshot may be lost. Undo history is retained while switching tabs, but is not restored across application restarts.

Normal closing still asks about unsaved changes. Discarded edits and untitled documents are omitted from the clean session; saved files reopen from disk on the next launch. Missing files are skipped with a notice. Malformed snapshots are backed up as `pocket_editor_session.invalid.json`. A file lock keeps a second editor instance from overwriting the first instance's recovery data; that instance runs without session persistence. The snapshot contains local document contents, including unsaved drafts.

Use `python3 -B pocket_editor.py --no-session` to launch without restoring or writing a session. A file supplied on the command line opens after session restoration. Run `python3 -B -m unittest -v test_editor_session` for the persistence and recovery checks.

## Quick Open

Press **Ctrl+P** (**Cmd+P** on macOS), choose **File → Quick Open…**, or search for Quick Open in the command palette. Type part of a filename or relative path; multiple search words and case-insensitive matching are supported. Use ↑/↓ and Enter, or double-click a result. Escape cancels. Files already open select their existing tab, preserving edits.

The search root comes from Project Explorer's selected project, otherwise the current file's folder, otherwise a folder picker. With no current file, the picker remembers its last selected folder for that editor session. Reopening Quick Open refreshes the index. File discovery runs in a background thread and skips `.git`, `.hg`, `.svn`, `node_modules`, `__pycache__`, `.venv`, `venv`, and `vendor`; directory symlinks are not traversed. Up to 50,000 files and 20,000 directories are scanned, and the first 200 matching results are shown. If a limit is reached, narrow the project folder. Unreadable folders are reported in the picker footer.

Run `python3 -B -m unittest -v test_quick_open test_command_palette` for discovery, cancellation, existing-tab reuse, and palette checks.

## Plugin Manager

Choose **Extensions → Plugin Manager…**, **Settings → Plugin Manager…**, or find it through Ctrl+Shift+P. Search by plugin ID or status, select a plugin, and inspect its path, registered commands, subscribed events, services, and latest loading or command error. Open source opens the file in an editor tab. Refresh rescans installed plugins; Load new plugins retries failed registrations and loads newly enabled plugins that have not already loaded.

Enable/Disable persists startup preferences in `disabled_plugins` in the editor settings. Disabling a loaded plugin takes effect after restarting; it does not remove running callbacks or existing menu commands mid-session. Enabling an unloaded plugin allows Load new plugins to load it. Edited plugins that already loaded require a restart. Failed settings writes roll back the requested toggle. Runtime errors remain visible until restart; successful registration clears a previous loading error.

In `--safe-mode`, use **Settings → Plugin Manager…** to disable a broken plugin, then restart normally. Loading remains disabled while in safe mode. The manager reports the current process's registered events and services, not a dependency manifest or every event a plugin might emit. Helpers beginning with `_` are excluded.

Run `python3 -B -m unittest -v test_plugin_manager` for startup preferences, safe mode, plugin communication inspection, error retry, and settings-failure checks.

## Inline syntax diagnostics

The **syntax_diagnostics** plugin checks saved Python (`.py`, `.pyw`, `.pyi`) and JSON (`.json`) filenames after a 450 ms typing pause. Checks use the current unsaved buffer, run in a background worker, and never execute user code. The first syntax error gets a red underline and a clickable line/column message below the editor. Fixing the error clears the marker. Results from a changed or switched document are discarded.

Search the palette for **Check Syntax**, **Go to Syntax Error**, or **Toggle Automatic Syntax Checks**. Manual checks remain available when automatic checks are off; that toggle lasts for the current session. Untitled and unsupported filenames are not checked automatically. Checks skip files above 200,000 characters. Python uses the running interpreter's compiler; JSON uses Python's built-in JSON decoder. These are syntax checks, not type checking, undefined-variable analysis, or project-wide diagnostics. Undo, saving, and file contents are unaffected by checking.

Other plugins can request an asynchronous check through `api.call_service("syntax_diagnostics.check")`, inspect `api.call_service("syntax_diagnostics.get_status")`, and subscribe with `api.on("syntax_diagnostics.updated", callback)`. The completion event contains `source`, `path` (string or None), and `result` with a status/message and line/column for syntax errors. The check service queues work and returns None. Disable the plugin through Plugin Manager if needed.

Choose Extensions → Load New Plugins or restart to enable it. Verify with `python3 -B -m unittest -v test_syntax_diagnostics`.

The Extensions toolbar dropdown groups commands by plugin to keep the menu compact. Add to toolbar uses the same plugin groups and shared checkmarks as the menubar. Plugins with more than 18 commands continue under More… rather than producing an oversized menu. Search Commands opens the command palette for direct access to every command. The menubar's Plugins menu remains available.

## Split editor

Choose **View → Split Editor Right**, or find that command with Ctrl+Shift+P. The current file stays on the left and another open file appears on the right. With only one document, the editor creates an empty second tab. Drag the divider to resize the panes. Both panes are editable and scroll independently, with line numbers and filename/unsaved indicators.

Click a pane to activate its document. Save, Undo, palette commands, plugins, and syntax diagnostics use that active document. Choosing a tab already visible focuses its pane; another tab replaces the active pane's file. **View → Focus Other Pane** switches panes. **View → Close Split** returns to one pane without closing any tabs or discarding edits. Closing a file still checks unsaved changes and collapses the split when only one document remains. Font, tab width, wrapping, and line-number preferences apply to both panes. Session restore retains documents and cursor positions; the split arrangement itself is not restored.

Verify with `python3 -B -m unittest -v test_split_editor`.

## Custom AI plugin example

**Extensions → My AI Example…** is an independent, customizable Chat/Code plugin with a configurable compatible endpoint, model, in-memory API key, explicit active-tab attachment, reply/input/time/request limits, and reply events/status services. It sends nothing at startup and does not change the existing free-model backend. Load New Plugins or restart to enable it.

See [the setup and customization guide](examples/custom_ai/README.md), [the prompt for building your own version with AI Code](examples/custom_ai/BUILD_PROMPT.md), and [the example source](pocket_editor_plugins/my_ai_example.py). Verification uses mocked responses rather than paid API calls. Local request/token caps are not monetary spending guarantees.

The custom AI example also supports **OpenAI Responses**. Choose Provider → OpenAI, enter an OpenAI Platform API key (or set `OPENAI_API_KEY`), refresh the model list, and choose a compatible text/coding model. The Responses transport uses `max_output_tokens`, optional reasoning effort, and `store: false`; existing compatible Chat Completions providers remain available. Keys stay out of saved settings. Model refresh and generation are explicit actions and share the local request cap. See [the OpenAI setup steps](examples/custom_ai/README.md#test-with-an-openai-platform-api-key). Codex subscription logins and agent execution are not implemented by this plugin.

The custom AI window now uses a conversation-focused layout with a separate **Settings** window for providers/keys/limits, a welcome screen, styled message blocks and code fences, a bottom composer, and Copy conversation. Enter sends; Shift+Enter adds a new line. Welcome suggestions never send automatically.

The custom AI composer's **+ Files** button attaches up to four UTF-8 text/code snapshots with removable chips. Files and the optional active tab share the input budget with prompt/history. Binary/image/PDF uploads are unsupported. Remove and reattach to refresh a snapshot; Reset context clears previously sent file content from local conversation history. Selecting attachments makes no API request.

## Project Terminal colors and portable defaults

Choose **Project Terminal → Settings…** inside the terminal, or search **Project Terminal Settings…** in the command palette. Editor Dark, Light, and High Contrast presets provide a starting point. Customize background, normal text, command headers, status messages, error exit messages, and all 16 ANSI base/bright colors. Font family/size, output encoding, ANSI rendering, and optional force-color behavior are user settings. Save & apply persists them under `_appearance` in `project_terminal_settings.json`, preserving saved per-project commands.

Portable defaults use UTF-8 decoding, the operating system's monospace font, ANSI rendering enabled, and force-color disabled. The existing system-shell selection stays native to the OS; no Bash-only commands are introduced. If a chosen font is unavailable on another computer, the system monospace font is used. For Windows tools that emit a legacy encoding, choose locale, cp1252, cp850, or cp437 as appropriate. Python child processes receive the selected output encoding. Encoding and force-color changes apply to the next process; appearance changes recolor retained output immediately.

The renderer handles standard/bright ANSI colors, indexed 256-color and RGB colors, bold, underline, reverse, resets, and sequences split across read chunks. Other terminal controls, including title/clipboard/hyperlink escape commands, are discarded rather than executed; carriage-return progress updates become separate lines. This remains an output-only runner, not a full interactive terminal emulator. Standard error remains merged with standard output; category coloring uses recognizable text patterns rather than claiming to identify the originating stream. Nonzero exit markers use the configured error color.

Programs may suppress colors when output is piped. Enable **Request colored output from programs (force color)** to set FORCE_COLOR, CLICOLOR_FORCE, and TERM for the child; it also overrides NO_COLOR for that process. Some tools still require their own `--color=always` option. Disabling ANSI interpretation removes ANSI styling from retained text as well as future output. Output remains bounded to 200,000 characters and 8,192 characters per long line, with at most 512 distinct ANSI styles. No OS-specific ANSI rendering library is required.

Plugin communication exposes `project_terminal.get_settings` and emits `terminal_settings_changed` after saving. ANSI interpretation follows the [xterm control-sequence reference](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html). Parser tests are platform-independent; GUI/process tests were run on Linux. Windows/macOS runtime testing remains outstanding.

```sh
python3 -B -m unittest -v test_terminal_colors test_editor.EditorTests.test_terminal_output_fallback_stop_and_shutdown
```

The main **AI Chat / AI Code** window now keeps the current reply intact instead of deleting its first 300 lines once the transcript exceeds 1,800 lines. Older transcript content is trimmed toward 200,000 characters without cutting the latest assistant/team reply; one large current reply can exceed that target. Code-fence formatting also survives consecutive assistant output chunks. **Reply tokens → Auto** keeps the existing conservative defaults; explicitly choose up to 8,192 for a larger requested reply cap. Provider limits/timeouts can still stop generation, and larger caps can increase usage. No extra provider request or larger default budget is introduced.

Terminal presets now include **GNOME Tango** (inspired by the [GNOME Tango color palette](https://wiki.gnome.org/Design/OS/Colors)) and **Modern Graphite**, a softer dark scheme with restrained blue, green, amber, and magenta ANSI colors. Modern Graphite is the default for new settings; saved custom colors are preserved. The settings dialog previews command/error text and all 16 palette colors before Save & apply. Each preset applies its complete ANSI palette, not just its background. Restore defaults selects Modern Graphite.

## Terminal output categories

**Terminal Settings → Categories** lets you choose separate colors for user commands/messages, system/info messages, warnings, errors, and successful completion. Automatic category coloring is enabled by default and can be disabled independently of ANSI rendering. Plain, unrecognized program output uses the normal foreground color. Settings apply to retained output as well as new lines.

Examples of recognized prefixes are `USER:`, `INPUT:`, `SYSTEM:`, `INFO:`, `DEBUG:`, `WARNING:`, `ERROR:`, and `SUCCESS:` (also bracketed equivalents). Common compiler `file:line: error:` / `warning:` messages and build/test success lines are recognized. Matching is heuristic, not an assertion about process privileges or stdout/stderr origin. Partial lines are reevaluated as chunks arrive. The runner's own directory/status messages, command echoes, and exit markers have explicit categories.

Program-supplied ANSI colors take precedence over inferred category colors, matching the palette-driven model described in [GNOME Terminal's color documentation](https://help.gnome.org/gnome-terminal/app-colors.html). GNOME palettes alone do not provide this plugin's automatic log-category classification. You can use both features, disable automatic classification to see only application colors, or disable ANSI rendering to use only category colors.

## About and tab appearance

**Help → About Pocket Editor…** (also in the command palette) shows **Somi Douth**, **somidouth@gmail.com**, a short editor description, and a Python/Tkinter technology credit. Publisher information is read-only; the About window provides Copy details and Close. Old local About override files do not replace the published author information.

Document tabs now use consistent active/inactive backgrounds and readable titles: white for selected clean files, muted light text for inactive files, and amber for unsaved files. Close buttons match the tab background. Selected, hovered, disabled, and inactive notebook tabs have explicit contrasting colors across plugin windows, including Terminal Settings and Plugin Builder. Split-pane headings use matching active/inactive title colors.

## Offline HTML documentation

Open [docs/index.html](docs/index.html) in a browser, or choose **Help → Documentation…** in the editor. The complete guide includes all bundled plugins, editor workflows, terminal categories/colors, both AI tools and provider setup, file attachments, PHP exercises, PluginAPI events/services, personal About information, shortcuts, local settings, and troubleshooting. Navigation, local search, code-copy buttons, light/dark themes, and print layouts need no server or external assets. Browser behavior under file URLs can restrict clipboard/storage; the guide falls back when those APIs are unavailable.

Rebuild after documentation or plugin command changes:

```sh
python3 -B tools/documentation/build_docs.py
python3 -B -m unittest -v test_documentation
```

The generator uses the Python standard library and reads source/Markdown without executing plugins. The HTML documentation is local; no website was published.

Project Explorer: Open Project selects a folder. Save Project writes a `.pocket-project` definition; Save Project As writes another copy. Load Project reopens a saved definition. These commands are available in Extensions and the explorer Actions menu. Definitions store a relative folder path when possible so the folder and definition can move together. Document contents are saved separately with File → Save or Save All.

## Git Project plugin

**Extensions → Git Project** opens independent Git tools for any local project. Use Browse or Use Explorer to select a folder, or open it through Project Explorer → Actions → Git Project. Team Project is not required.

Clone creates a local repository copy. Choosing a folder initializes Git automatically when needed and registers the working-tree root for other plugins. Set Git Folder explicitly prepares a typed folder too. Enter a Remote URL and choose Set Origin to configure its shared repository. Refresh lists changed files; save document edits, select files, enter a message, and choose Commit Selected. Push publishes commits and configures the first upstream on origin when needed. Pull only fast-forwards and requires project tabs closed and local changes committed. Git authentication and author identity use your existing Git configuration.

**Git Project → Settings…** edits repository-local author name/email, origin URL, line-ending handling (`core.autocrlf`), executable-bit tracking, and GPG commit signing. Reload reads the selected repository; Save Settings persists changes in its Git config. Blank fields and Inherit remove local overrides and fall back to inherited Git settings. Saves are atomic, preserve unrelated options, and respect Git’s configuration lock. Global Git configuration is unchanged.

Other plugins can call `git_project.ensure_repository(folder)` to initialize Git if absent and register its root, without opening a window. `git_project.get_repository()` retrieves the registered root; `get_project()` is a compatibility alias. `git_project.open_project(folder)` prepares the folder and opens Git tools. Project Explorer remains optional; Browse works independently.

## Team Project, client invitations, and group chat

**Extensions → Team Project** opens hosted project collaboration, group chat, and shared files. Project Explorer → Actions → Team Project opens it for the selected folder. See [Team setup guide](TEAM_WORK.md).

Choose **Host a project** or **Join as client** in Team Project. The host selects a shared project folder, keeps the default LAN listen setting (`0.0.0.0:8765`) or chooses another port, and clicks Start Host. The actual port and a suggested LAN invitation address appear automatically. Under Project Copies → Client Keys & Invitations, generate a key for a named client, enter the client-facing server URL, and choose Export Client Invitation. Send the resulting `.team-invite` file to that client using your chosen transfer method. The file carries the server address, group/room, client name, client key, and project name. It excludes the host administration key and host-local paths.

**Easy Internet Setup…** detects the host OS/CPU and can install checksum-verified cloudflared in the user's tools folder, then start a temporary Cloudflare HTTPS relay. Start Host first, open Easy Internet Setup from Setup or Project Copies, then click Start Internet Sharing. After the app verifies the relay against the authenticated host, the public URL is used automatically in client invitations. Clients need only the invitation; no relay installation or IP/port entry is needed on their computers. Stop Sharing or stopping the host stops the owned tunnel. Temporary URLs change after restart and have no uptime guarantee; use a named tunnel for a stable production address. The setup panel also exports Linux/macOS shell or Windows PowerShell commands/scripts. See [the full setup guide](TEAM_WORK.md).

**Detect My Public IP** is at the top of Team Project, before Host/Client setup, and remains visible when Setup is collapsed. Either role can use it immediately without connecting or opening another window. Copy IP copies the detected address. Lookup tries ipify, AWS CheckIP, then Cloudflare icanhazip over HTTPS, using a four-second timeout for each request and the first valid public IPv4 result. DNS failures, connection errors, TLS verification failures, and invalid responses trigger a fallback; if all fail, the visible message identifies the problem and the button can be retried. No project content or join key is sent, and lookup runs only when clicked. The same result is shared with Details and Client Keys & Invitations. Use HTTPS IP explicitly applies it to an invitation; existing server URLs are preserved until then.

The client needs only the host invitation and a local folder; its own outgoing port is automatic. The client clicks Load Invitation, selects that file, chooses their local project-copy folder, then clicks Join Team. Loading a file does not connect automatically. The initial shared project downloads on joining. Pull Project receives updates; Send Changes to Server publishes saved edits. Conflicting files are preserved locally and reported. Close project tabs before syncing.

**Details** shows the host endpoint, suggested local IP, and the client source address observed during a connection check. Optional public-IP lookup is available to either role. The host can refresh its client list to see last observed IPs, automatic source ports, and request times. Proxies may appear as peer addresses; client source ports are not listening ports.

The group conversation uses incoming/outgoing message cards, sender names, timestamps, date separators, and attachment cards with Save actions. Enter sends; Shift+Enter inserts a new line. Connection settings collapse after joining. Audio, video, and other files can be exchanged up to 50 MiB each, with a Shared Files tab for the attachment list. Recorded media is exchanged as files; live calls are not provided.

The host retains history, attachments, and prior versions of files replaced by uploads. Each client key can be revoked. Invitations contain a client access credential and should be given only to that client. Chat hosting lasts until Stop Host or editor exit; a dedicated server is also supported. LAN/VPN addresses can use private HTTP; internet access needs HTTPS through a proxy. Git Project is a separate optional plugin.

Host mode now includes direct **Install Relay**, **Start Internet Sharing**, and **Stop Sharing** buttons in the main Internet Sharing panel. They perform the setup/start commands without a terminal or extra dialog. The panel stays visible when Setup collapses after starting the host; Start Internet Sharing becomes available once the host is running. Installation is also automatic when starting sharing if the tool is missing.

## Source release

Author: **Somi Douth** · [somidouth@gmail.com](mailto:somidouth@gmail.com).

Build a portable source archive with `python3 -B tools/build_release.py`. It includes the editor, plugins, packaged AI backend source, optional PHP dependency manifests, examples, tests, and offline Help. It excludes private settings, recovery drafts, invitation keys, caches, installed packages/executables, old backups, and local demo projects. See [Publishing](PUBLISHING.md) and [release notes](RELEASE_NOTES.md).
