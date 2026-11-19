# Customize AI Assistant

There is one AI Assistant plugin: `pocket_editor_plugins/ai_assistant.py`. The separate My AI Example plugin and old AI Chat/AI Code frontend have been removed.

For complete setup and a Google Gemini example, read [AI Assistant](../../docs/AI_ASSISTANT.md). Open Extensions → AI Assistant… → How to use for the same guide inside the GUI.

## Extension points

- `PRESETS`, `KEY_VARIABLES`, and `PROVIDER_HELP`: provider endpoints, key environment variables, and setup instructions.
- `validate_config`: protocol/URL validation and local limits.
- `request_json`: bounded JSON transport with redirects blocked and protocol-specific key headers.
- `send_request`: OpenAI Responses, OpenAI-compatible Chat Completions, and native Gemini request/response conversion.
- `list_models`: model discovery; Gemini filtering includes text-generation-capable models.
- `AIAssistant`: Chat/Code interface, explicit attachments, background requests, and events.

Services: `ai_assistant.get_status` returns busy/request/cooldown state. Successful replies emit `ai_assistant.reply` with text and model. API keys and attachment contents are never stored in settings. Other loaded plugins can receive the reply event, so enable only plugins you trust.

Run `python3 -B -m unittest -v test_ai_assistant` for offline tests. These do not contact real providers or spend API credits.
