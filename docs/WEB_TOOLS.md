# Web Tools — HTML, CSS and JavaScript

Web Tools adds local formatting, context-aware completion and syntax checks for web development. Open **Plugins → Web Tools** or pin **Web Tools** under **Settings → Vertical plugin tabs**. Its panel scrolls so settings and status remain accessible in a short window.

## Setup

1. Install Node.js LTS, including npm, from [nodejs.org](https://nodejs.org/), then restart Pocket Editor.
2. Open Web Tools and click **Install / Repair Tools**. Installation downloads pinned packages into the editor's `tools/web` folder. Lifecycle scripts are disabled. Installation needs an internet connection; normal editing operations run locally.
3. Open or save an `.html`, `.htm`, `.css`, `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.tsx`, `.scss`, or `.less` file.

For a source checkout, this is equivalent to:

```sh
npm ci --prefix tools/web --ignore-scripts --no-audit --no-fund
```

The core editor does not require Node.js, and the web dependencies are separate from PHP tools. Loading this plugin does not launch Node or install packages. Missing dependencies produce a setup message in the panel.

## Formatting

Click **Format document**, use **Plugins → Format Web Document**, or press **Ctrl+Shift+F** (Command on macOS). Prettier formats the current unsaved document; it does not write the file. **Ctrl+Z** reverts formatting and explicit **Save** writes it.

Choose indentation (2/4), spaces/tabs, print width, single-quote preference and JavaScript semicolons, then click **Save settings**. Options are stored in `web_tools_settings.json`. Project JavaScript configuration files and custom Prettier plugins are not executed. Invalid input is left unchanged and its parser error appears in the status area.

## Autocomplete

Press **Ctrl+Space** or choose **Web Autocomplete**. Automatic completion is enabled by default after a short typing delay and can be disabled in the panel. Use arrow keys to select, Enter/Tab to accept, Escape to close. Supported snippet placeholders can be traversed with Tab and Shift+Tab; mirrored placeholders are filtered out.

- HTML: tags, closing tags, attributes and attribute values.
- CSS, SCSS and LESS: properties, values, selectors and language-service snippets.
- JavaScript/JSX and TypeScript/TSX: local symbols, object members and DOM/browser APIs from TypeScript's language service.
- HTML `<style>` and `<script>` blocks: CSS and JavaScript completion inside the block. Attribute-level embedded CSS/JavaScript completion and framework-specific Vue/Angular/Svelte templates are not implemented.

JavaScript imports can resolve saved code and type declarations in the selected project, plus unsaved open JavaScript/TypeScript documents. If the active file is outside the Explorer project, its folder is used instead. The tool does not scan the whole workspace or execute application code. The first JavaScript request warms the language service; later requests reuse the process. This is a lightweight browser-oriented configuration, not full `tsconfig.json`, bundler, framework or auto-import support.

## Syntax checks and colors

Click **Check syntax** to check unsaved input. JavaScript/TypeScript uses syntactic diagnostics; CSS-family files use language-service validation. Prettier adds parser checks, including structural HTML errors. This is not ESLint, semantic type checking, accessibility auditing or complete HTML validation.

Errors appear as red zigzag markers and warnings in amber. Select an issue in the panel to jump to it. Editing or switching documents clears stale markers. **Code Theme → Auto** recognizes HTML and CSS in addition to JavaScript, with colors matching the chosen editor palette.

## Responsiveness and limits

Formatting, installation and completion run outside the Tk GUI thread. A loading indicator remains active until an explicit action's result is applied. Automatic suggestions do not show a full loading overlay. Changed documents and stale completion captures are never overwritten. The language process is stopped when the editor closes or the plugin is cleaned up.

Tools pause above 300,000 characters. Completion returns at most 120 items. At most 32 open files and 2 million characters of open-buffer data are supplied for context; individual imported files are limited to 1 MB. Requests time out after 20 seconds, and setup after 180 seconds. No requests are sent to an AI provider.
