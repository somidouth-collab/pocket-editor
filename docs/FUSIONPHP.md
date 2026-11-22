# FusionPHP template tools

This local plugin supports FusionPHP `.fpf` templates: syntax highlighting, formatting, context-aware autocomplete, syntax checks, and red zigzag error marks. It follows the Template v2 compiler inspected in the local FusionPHP project. It does not modify the framework, render templates, start PHP, read application configuration, or execute PHP/controller/model code. No additional package or language server is required.

## Open the plugin

Restart the local editor with `python3 -B pocket_editor.py`, then choose **Plugins → FusionPHP Tools → FusionPHP Tools**. You can also enable **FusionPHP Tools** in **Settings → Vertical plugin tabs…**. The menu and vertical tab reuse one embedded panel, and hiding/reopening it preserves its state.

Open a `.fpf` file from your application or try `examples/fusionphp/app/home/home.fpf`. This editor example is not a standalone framework application.

## Theming and highlighting

FusionPHP Tools applies its own colors automatically whenever a `.fpf` document is active. It works even with an older Code Theme plugin, a saved **Plain text** language choice, or restored white foreground formatting. HTML tags are blue, directives purple, data fields/attributes cyan, helpers yellow, strings orange, and comments green with the Dark palette. It follows the current editor background and adapts to Light, Dracula, and Solarized Dark as well.

**FusionPHP Theme** opens Code Theme’s palette selector when that plugin is enabled, including older installations that do not expose a settings service. To turn template colors off, clear **Color FusionPHP templates** in **FusionPHP Settings**. This changes editor presentation only and never modifies template text or the framework.

## Formatting

**Format FusionPHP Template** validates the current buffer and adjusts structural indentation using the number of spaces in **FusionPHP Settings**. It does not rewrite expressions, wrap visible text, change inline spacing, or modify the contents of `pre`, `code`, or `textarea`. Multiline attributes and template literals are preserved. Indentation is capped at 64 structural levels to bound output size.

Invalid templates are left unchanged. Formatting is one undoable edit and never saves the file automatically. Save writes it when you are ready. If the text or active tab changes during formatting, the old result is discarded.

## Autocomplete

Press **Ctrl+Space**, or type within `<…`, `{{ … }}`, or `{% … %}`. Use Up/Down to select, Enter/Tab to insert, and Escape to close. Simple snippets select their first placeholder; full placeholder navigation is not implemented.

Completions include supported HTML names/attributes, `extends`, named blocks, `if`/`else`/`endif`, `for`/`endfor`, layout `yield`, and the engine's `trim`, `length`, `default`, `join`, and URL-context `url` helper. There are no Blade/Twig/PHP directives added to the language.

The plugin reads bounded `*Model.php` and `*Controller.php` source files under the containing project's `app` directory and extracts literal array keys. It skips application configuration, `.env`, vendor/dependency/cache folders, and symbolic links. It does not evaluate PHP. This is a best-effort static index, not type inference: unrelated literal array keys may also appear, and dynamically created fields cannot be inferred. It reads at most 40 source files of up to 200 KB each, with a 2 MB total cap.

For example, an array key `guides` with nested `title`/`url` fields offers `guide.title` and `guide.url` inside `{% for guide in guides %}`. Additional dotted fields can be entered one per line in Settings. Layout names come from `app/layouts/*.fpf`.

## Syntax checks and red zigzag marks

**Check FusionPHP Syntax** checks unsaved editor text. Automatic checking runs after a short edit pause. The first compiler-style syntax error appears in the panel with a red zigzag underline. Double-click the issue or click its underline to jump to it. Stale markers disappear when you edit or switch documents. The markers follow scrolling, wrapping, and theme changes.

The checker verifies delimiters, the restricted HTML vocabulary, interpolation contexts, expression/helper arguments, layout/page rules, unique blocks, matching control directives, and the template nesting limit. As in the framework, missing data fields are allowed; the checker does not pretend to know runtime values or validate the whole HTML document structure.

**Auto** treats files directly under a `layouts` folder as layouts. Choose **Page** or **Layout** explicitly in Settings for templates kept elsewhere. Layouts can contain `{% yield content %}`; pages cannot. A page extending a layout must contain only unique named blocks after its `extends` declaration. Layout existence, database results, PHP validity, runtime field types, and rendering output are outside syntax checking.

Valid examples:

```html
{{ title }}
{{ trim title }}
{{ length items }}
{{ default title 'Untitled' }}
{{ join labels ', ' }}
<a href="{{ url target }}">{{ title }}</a>
{% if user.active %}<p>Hello</p>{% else %}<p>Welcome</p>{% endif %}
{% for item in items %}<p>{{ item.title }}</p>{% endfor %}
```

URL attributes (`href`, `src`, `action`, `formaction`) require a whole quoted `url` expression. Ordinary supported dynamic attributes also require one expression as the entire quoted value. Use the opposite quote style for literals inside attributes. Raw PHP, arbitrary function calls/operators, script/style elements, and unsupported directives are rejected by the inspected framework grammar.

Tools pause above 300,000 characters. All worker results are checked against their captured document before applying. Loading remains visible through background work and result rendering. Settings are saved locally in `fusionphp_tools_settings.json` and excluded from publication.

## Other plugins

GUI-thread callers can invoke `fusionphp_tools.format`, `fusionphp_tools.complete`, and `fusionphp_tools.check_syntax`. These schedule background work for the active `.fpf` document.
