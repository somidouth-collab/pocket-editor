# C/C++ Tools

One plugin provides formatting, clangd autocomplete, syntax checks, red zigzag error underlines, and GDB debugging. Open **Plugins → C/C++ Tools**, or pin **C/C++ Tools** using **Settings → Vertical plugin tabs…**. Menu commands, the command palette, and the vertical tab all open the same embedded panel. Hiding it or switching tabs preserves its inputs, diagnostics, and debugger data; reopening does not create a detached window. Settings and file pickers remain normal dialogs.

## Install the tools

On Debian, run this in your terminal:

```bash
sudo apt install clang-format clangd gdb build-essential
```

Restart the editor after installation, then open **C/C++ Settings → Detect tools → Save Settings**. Blank executable fields search PATH, supported versioned LLVM names, and the standard LLVM installation directory on Windows. Enter full executable paths when automatic discovery cannot find a tool. Settings never execute installation commands automatically.

On Windows, install LLVM for clang-format/clangd and a GCC-compatible toolchain with GDB, such as MinGW-w64 through MSYS2. Select its compiler and GDB executables in Settings. MSVC's `cl.exe` and the Visual Studio debugger are not supported. On macOS, install LLVM/GCC-compatible tools; GDB requires appropriate permissions/code signing. LLDB is not supported. Windows/macOS runtime behavior has not been verified.

## Formatting

Open a saved `.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.c++`, `.hh`, `.hpp`, `.hxx`, `.ipp`, or `.tpp` file. **Format C/C++** formats the current editor text with clang-format. The default `file` style searches for the project's `.clang-format`, with LLVM as fallback. Other built-in styles are available in Settings.

Formatting changes the editor buffer. Undo reverts it, and explicit Save writes it to disk. If you edit the document or switch tabs while formatting is running, its result cannot replace newer work. Files over 4 MiB are not sent to these tools.

## Autocomplete

Type at least two identifier characters, `.`, `->`, or `::` to request automatic suggestions. **Ctrl+Space** requests suggestions explicitly. Use Up/Down to select, Enter or Tab to insert, and Escape to dismiss. Suggestions use the current unsaved buffer. Supported completion edits and simple snippets are applied as one undoable change; the first snippet placeholder is selected. Full snippet tab-stop navigation is not implemented.

clangd starts on first request. The first request can take longer while the project indexes. If a tool fails, automatic completion pauses until a manual request or Settings save, avoiding repeated failures while typing.

For project-aware completion, generate `compile_commands.json`. For CMake projects, configure the existing build with `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`; this plugin does not configure or build a framework automatically. clangd can discover compilation databases in parent directories and `build/`, or you can select a compilation database directory in Settings. Fallback standards and include directories apply when there is no compilation database entry. `.h` files default to C++ in this plugin; `.c` is C and uppercase `.C` is C++.

Other files, including headers, are read from disk. Save changes to them before expecting those changes to appear in another file's suggestions.

## Syntax errors and zigzag underlines

**Check C/C++ Syntax** runs the configured GCC-compatible compiler in syntax-only mode against the current editor text. Automatic checking runs after a short typing pause. It does not build or run your program.

Errors receive red zigzag underlines; warnings receive amber ones. The **Errors & warnings** list shows the message and line. Double-click an issue, or click its underline, to jump to it. Markers clear when the checked text changes and are redrawn after the next check. They follow scrolling and line wrapping.

Syntax checks use the standard and include directories in this plugin's Settings. They do not import flags, macros, or include paths from `compile_commands.json`. Configure equivalent includes here for simple projects; a full project build remains the final check for projects with additional build flags. GCC/Clang column reporting can vary for unusual Unicode text.

## Debugging

1. Save the source before building. **Build file** compiles the active file with `-g -O0` into `.pocket-debug` in the project directory. It is a single-file build; projects containing multiple source files should use their existing build system with debug symbols, then **Browse…** to select the resulting executable.
2. Put the editor cursor on a source line and choose **Toggle C/C++ Breakpoint**, or the panel's **Breakpoint** button. Choose it again on that line to remove the breakpoint. Breakpoints currently last for the editor session and do not relocate after edits.
3. Open the **Debugger** tab, choose the program and optional arguments separated with `|`, then **Start**. Without explicit breakpoints, the debugger initially stops at `main`.
4. Use **Continue**, **Pause**, **Step over**, **Step into**, and **Step out**. Stopping on a source line selects it in the editor. Variables and the call stack update while stopped; double-click a stack frame to inspect its variables. Enter an expression and click **Inspect** to evaluate it.
5. **Stop** closes this plugin's debug session. Closing the editor also stops its owned tools. After a program exits, Stop releases the session before starting another.

Only launch executables you intend to run. Program output is displayed in the panel. Interactive program input, attaching to existing processes, remote debugging, LLDB, and visual variable expansion are not implemented. GDB expression evaluation can call program functions; use expressions you intend to evaluate. Edit/build operations never modify AOF or another framework automatically.

Try `examples/cpp/main.cpp` for a small standalone example: complete `counter.`, build the file, place a breakpoint at `counter.increment()`, then step and inspect `counter.value`.

## Plugin services

Other plugins can call `cpp_tools.format`, `cpp_tools.complete`, and `cpp_tools.check_syntax` on the GUI thread. These start background work for the current document; formatting and completion results are applied only if their captured document still matches.

## References

- [clang-format documentation](https://clang.llvm.org/docs/ClangFormat.html)
- [clangd installation and project setup](https://clangd.llvm.org/installation.html)
- [GDB manual](https://sourceware.org/gdb/current/onlinedocs/gdb/)

Loading indicators stay active through worker completion, result rendering, and the display handoff. Autocomplete and syntax checks also show loading during automatic requests. Debugger initialization and paused-state inspection keep their indicators until the required data is ready.
