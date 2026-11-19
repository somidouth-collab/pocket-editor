# Codex Assistant in Pocket Editor

## Setup once

1. Install Codex CLI using `npm install -g @openai/codex` (requires Node.js/npm), or use the official Codex installation instructions at https://developers.openai.com/codex/cli/.
2. Run `codex login` in your own terminal and complete the login there. `codex login status` checks your login without starting a task. The editor uses this existing CLI authentication; do not paste passwords or tokens into the editor.
3. Open **Extensions → Codex Assistant… → Detect Codex**. If detection fails, browse to an installed executable. Windows npm installs normally provide `%APPDATA%/npm/codex.cmd`; the plugin invokes the adjacent Codex JavaScript entry with Node rather than running a shell shim.
4. Choose your actual project folder. An open Explorer/Git project is suggested automatically. Leave Model empty to use your CLI default.

## Your first request

Keep **Allow Codex to edit saved files** unchecked. Enter `Explain the structure of this project briefly` and click **Send to Codex** (or Ctrl+Enter). The shared orbit animation appears while Codex is working. The result appears in the output panel. **Open reply in new tab** copies the reply into an unsaved editor document.

**Attach current tab snapshot** sends the current text, including unsaved edits, in the request. It is off by default. The prompt and tab snapshot together are limited to 64 KiB. Codex may inspect files in the chosen project through its CLI tools and follows applicable project instructions. Read-only mode prevents writes under the CLI sandbox; it is not a restriction on what project content the model may read.

## Editing a project

Save your work and close tabs belonging to that project before enabling **Allow Codex to edit saved files**. Then describe a small change and Send. The CLI uses its `workspace-write` sandbox for that explicitly selected task. Reopen changed files and review/test them afterward. Stop does not revert changes already made.

The plugin sets approval policy to `never`, so commands that require interactive approval fail instead of opening an invisible prompt. It never requests unrestricted execution. Your CLI installation, sandbox availability, configuration, MCP connections, and project rules still apply. If a task cannot run in this mode, use Codex in a terminal to handle its interactive workflow.

## Requests, limits, and stopping

Each Send starts a separate CLI task; earlier editor replies are not automatically attached as conversation history. The plugin does not choose a different model unless you fill the Model field. Requests use your own Codex account/API configuration and limits; no balance transfer or automatic retry is performed. There is no live request in the plugin tests.

**Stop** terminates the owned CLI process tree. Closing the editor also stops it. Hiding the Codex window leaves the request running. A five-minute timeout stops long tasks; for larger work use Codex in a terminal. Replies are bounded to 1 MiB. The plugin does not save prompts/replies itself, but Codex CLI may retain session information according to its own configuration.

If a task fails, run `codex --version` and `codex login status` in a terminal, check CLI model/sandbox configuration, and try a short read-only request. Optional/custom CLI flags are not passed through by this plugin.
