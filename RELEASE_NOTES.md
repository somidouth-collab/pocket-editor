# Pocket Editor — 2026.11.19

Created by **Somi Douth** · somidouth@gmail.com

- Replaced the old AI Chat/AI Code frontend and bundled backends with a single **AI Assistant**, promoted from My AI Example.
- Added native Google Gemini requests and text-model discovery, correct key headers, setup hints, and an in-app **How to use** guide with a Gemini example.
- Added provider-specific OpenAI, Gemini, Ollama, OpenRouter, Groq and DeepSeek presets and environment-variable guidance. Provider keys stay in memory, are isolated on preset switches, and clear when the endpoint host changes.
- Failed AI requests restore the submitted prompt when the composer is empty. Request limits and rate-limit pauses remain explicit; no automatic paid retries are sent.
- Consolidated Python formatting and Python/JSON syntax checks into **Python Tools**, and PHP formatting/completion into **PHP Tools**. Existing language-tool settings files and commands are retained.
- Added **Git setup** with PATH/standard-folder/Windows-registry discovery, custom executable selection, version verification, official installer access, and optional winget installation for Windows 10/11. Git operations use the selected executable and hide console windows on Windows.
- Updated source and PyPI packaging checks, offline documentation, and installation instructions.

## Migration

The `my_ai_example` plugin no longer exists; use `ai_assistant.get_status` and the `ai_assistant.reply` event. Non-secret My AI Example settings are read as a fallback when no new AI Assistant settings file exists. Keys are never migrated from disk.

The old `python_formatter` and `syntax_diagnostics` plugin entries are replaced by `python_tools`; services are `python_tools.format`, `python_tools.check`, and `python_tools.get_status`. The existing `syntax_diagnostics.updated` event is retained. The old `php_formatter` and `php_completion` entries are replaced by `php_tools`; services include `php_tools.format`, `php_tools.busy`, `php_tools.complete`, and `php_tools.status`. Re-add language-tool toolbar shortcuts if needed because their plugin IDs changed.

PyPI runtime folders are separate per version. Upgrade with `pipx upgrade pocket-editor`. A new runtime version does not overwrite your older runtime folder or its personal settings.

## Verification scope

GUI and tool regression tests run on Debian Linux. Native Gemini payload/authentication, model-catalog filtering and reply parsing are tested offline without API credits. Live Gemini account access and native Windows installation have not been verified; Windows discovery/installation command selection has mocked automated coverage. The license remains undecided.
