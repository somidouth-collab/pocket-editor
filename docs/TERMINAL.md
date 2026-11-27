# Project Terminal — interactive shell and unified menus

Open **Plugins → Project Terminal**, or its vertical tab. A single menu bar contains **Terminal**, **Edit**, **Project**, **View** and **Settings**. The old folder fields and Run/Stop/Settings toolbar no longer occupy the terminal surface. Their functions remain accessible from these menus.

## Interactive shell

The default view starts your system shell in a real pseudoterminal. Type commands directly into the terminal. The shell retains `cd`, exported variables, history and foreground jobs. Arrow keys, Tab completion and interactive input work according to the selected shell; shells such as Bash provide richer editing than plain `sh`.

The emulator handles ANSI colors, cursor movement, carriage-return progress updates, clear-screen controls, resize, scrollback and alternate-screen switching for ordinary terminal applications. It is a lightweight VT-style terminal, not a complete xterm replacement: advanced graphics, terminal mouse protocols and every full-screen application are not guaranteed.

- **Terminal → New / Restart Shell:** starts a fresh shell in the selected directory. Restart terminates the previous session and its foreground job.
- **Terminal → Interrupt:** sends Ctrl+C to the foreground application.
- **Terminal → Stop Shell:** ends the current shell and its foreground job.
- **View:** switches between Interactive Shell and the retained Command Output buffer. Switching views or plugin tabs does not restart the shell.
- **Project:** selects a directory, uses the current Explorer project, or manages saved commands. Directory choices apply to the next shell restart and to the command runner; a shell's own `cd` remains part of that session.

The default shell comes from `$SHELL` on POSIX. Windows prefers PowerShell 7 if installed, otherwise the system command processor. **Settings → Shell…** chooses another executable. Leave it blank to use the default; arguments are not accepted in this field. Restart the shell after changing it.

## Keyboard and clipboard

Type directly in Interactive Shell. Enter sends input, arrows navigate, and Tab is forwarded to the shell. Ctrl+C interrupts; Ctrl+D sends end-of-input on POSIX. Ctrl+Shift+C copies selected text. Ctrl+Shift+V, Ctrl+V or Shift+Insert pastes. Bracketed paste is used when requested by the shell. Individual pastes are capped at 65,536 characters, including terminal wrappers.

Selected text remains visible above ANSI colors, including while the terminal is unfocused. While selecting text or using its right-click menu, screen redraws wait so copied text stays stable; incoming output continues to be processed. Click outside the selection or type to return to the latest screen. The context menu opens on mouse release and preserves the selection.

Use the mouse wheel or Shift+PageUp/PageDown for screen-history paging. **Edit → Copy / Paste / Select All / Clear** provides the same essentials. Clear resets the client screen/history and requests a shell redraw. Terminal keyboard input does not modify editor documents.

## Preserved command runner

**Terminal → Run Command…** opens the working-directory and command dialog. Browse and Current Project remain available there. Run executes a separate system-shell command and selects **Command Output**. It retains the existing ANSI/category colors, bounded output, exit-code markers and stop behavior. This mode does not accept interactive stdin; use Interactive Shell for prompts and REPLs.

**Project → Save Project Command** remembers the current command per directory. **Terminal → Run Saved Command** repeats it; **Stop Command** stops that command's process group/tree. The interactive shell and command runner are separate sessions, so a shell `cd` does not change the runner's selected directory.

## Existing appearance settings

**Settings → Appearance…** retains presets, background/text colors, command/status/error/warning/success colors, the 16-color ANSI palette, font, encoding, ANSI interpretation, category coloring and force-color. Existing `project_terminal_settings.json` settings and saved project commands are preserved. Appearance changes apply to both terminal views. Encoding/force-color changes apply to subsequently started processes.

## Engine setup and platforms

The batch command runner still works with Python and Tk alone. Interactive mode uses optional `pyte` plus `ptyprocess` on POSIX or `pywinpty` on Windows. **Terminal → Install / Repair Engine** installs them into the editor's isolated `tools/terminal/.venv`. Setup needs internet access; subsequent terminal sessions run locally. Stop the shell before repairing the engine.

For a source checkout:

```sh
python3 -m venv tools/terminal/.venv
tools/terminal/.venv/bin/python -m pip install -r tools/terminal/requirements.txt
```

Windows uses the corresponding `.venv\Scripts\python.exe`. The Python environment is local runtime data and is excluded from release archives. On POSIX, the PTY is spawned in a separate helper process before that helper creates threads, avoiding a fork of the multithreaded editor.

Real shell, input, resize, Ctrl+C, foreground-process cleanup, screen controls and settings tests run on Linux. The Windows backend uses pywinpty; native Windows interactive behavior still needs platform verification. Deliberately detached applications are outside normal terminal cleanup guarantees.
