# Build your own AI Chat/Code plugin

`pocket_editor_plugins/my_ai_example.py` is a working, standalone example using only Python's standard library and the editor's PluginAPI. It does not depend on the bundled free-model AI scripts and does not send requests at startup.

## Run the example

1. Restart Pocket Editor, or choose Extensions → Load New Plugins.
2. Open Extensions → My AI Example…, or find it in Ctrl+Shift+P.
3. Click **Settings**. Choose an OpenAI, Ollama, or OpenRouter provider preset, or choose Custom and select the API format. Enter the provider's exact **model ID**, or click Refresh models and choose a compatible text model. Enter an **API key** where required.
4. Set the reply-token, input-byte, network-timeout, and session-request limits.
5. Choose Chat or Code, type a message, and press Send.

For local Ollama, the default endpoint is `http://127.0.0.1:11434/v1/chat/completions`. Run Ollama separately and enter a model ID you have already installed. Leave the key empty for a local server that does not require authentication.

For OpenRouter, use `https://openrouter.ai/api/v1/chat/completions`, your OpenRouter API key, and a model ID available to your account. Other providers must support this example's OpenAI-compatible `messages`, `max_tokens`, `stream: false`, and text `choices[0].message.content` format. Native Anthropic, Gemini, streaming, tool calls, images, and provider-specific authentication are not implemented by this example; use a compatible gateway or adapt `send_request` for that provider. Some models require different token-limit parameters.

Provider references:
- [Ollama OpenAI compatibility](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx)
- [OpenRouter chat-completions API](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)
- [OpenRouter authentication](https://github.com/OpenRouterTeam/docs/blob/main/api_reference/authentication.mdx)

## Conversation interface

The main window keeps the conversation in focus: a compact Chat/Code header, model summary, welcome prompts, distinct user/assistant messages, monospaced fenced code blocks, and a bottom composer with an attachment toggle and Send button. **Settings** opens provider, credential, model discovery, and limit controls in a separate non-modal window. **Done** hides Settings; Save settings persists only non-secret configuration.

Press Enter to send or Shift+Enter for a new line. Welcome prompts only fill the composer; they do not make requests. **Copy conversation** preserves the visible transcript as plain text, including code fences, while **Open reply** copies only the latest reply into a new unsaved editor tab. Reset context clears model history and keeps the visible transcript. Closing the chat hides both its window and Settings, preserving the session for reopening. The layout adapts down to 640 × 540 without changing the editor's shared fonts or styles.

## Test with an OpenAI Platform API key

1. Restart the editor after updating the plugin, then open **My AI Example…**.
2. Open **Settings**, then choose **Provider → OpenAI**. This selects **OpenAI Responses** and `https://api.openai.com/v1/responses`.
3. Paste your **OpenAI Platform API key** into the memory-only key field, or launch the editor with `OPENAI_API_KEY` set. A Codex/ChatGPT subscription login token is not a replacement for a Platform API key. This plugin does not read Codex credentials, use subscription allowances, or implement Codex CLI/SDK agents.
4. Click **Refresh models**. This explicitly calls `GET /v1/models`, does not generate a reply, and counts against the plugin's local request cap. Choose a text/coding model your account can use with Responses, or type its exact API ID. Catalog entries can include non-text models or models requiring different APIs; discovery does not prove request compatibility. No model ID is hardcoded or automatically selected.
5. Leave **Reasoning → default** initially. Choose low/medium/high only when the model supports that value. Some reasoning models may use the output cap for reasoning and return little or no visible text; increase the cap explicitly if needed. The plugin never retries automatically.
6. Set a small session request limit, choose Chat or Code, and send a short test prompt such as “Reply with one short sentence.” Keep Attach current tab off for the first test.
7. Save settings if desired. Keys and conversations are excluded. Check your Platform usage/billing dashboard for actual usage; the local request/token controls are not a monetary budget.

The Responses request uses `instructions`, text-message `input`, `max_output_tokens`, `stream: false`, and `store: false`; explicit reasoning adds `reasoning.effort`. Conversation context is resent from local memory without previous-response IDs. The reader collects message output-text parts, handles refusals as visible text, reports incomplete replies, and ignores non-text reasoning/tool items. `store: false` requests that the response not be stored for later API retrieval; it is not a promise about all provider retention policies.

Authentication/access errors, invalid parameters, missing endpoints/models, credits/quota issues, and rate-limit pauses are displayed without revealing error bodies or keys. Refreshing models does not require a model ID; sending does. No request is made merely by choosing a preset or saving settings.

Official references: [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart), [Responses creation](https://developers.openai.com/api/reference/resources/responses/methods/create), [model listing](https://developers.openai.com/api/reference/resources/models/methods/list), and [authentication options](https://developers.openai.com/codex/auth).

## Keys and settings

The password field holds the key only in memory. You can also set `OPENAI_API_KEY` for the OpenAI preset or `CUSTOM_AI_API_KEY` for compatible providers before launching the editor. Changing presets clears the previous provider’s typed key, then reads the corresponding environment variable. Save settings writes only API format, reasoning setting, endpoint, model, and limits to `my_ai_example_settings.json` beside the plugin. It does not save the key or conversations. Closing the window hides it, preserving the in-memory session and key until editor exit; reopening raises it. Change/remove the key directly in its field.

Remote endpoints require HTTPS. Plain HTTP is accepted only for local loopback addresses. Credentials in URLs and redirects are rejected. Error bodies are not displayed, preventing accidental disclosure of provider response details. The editor makes one request at a time, only after Send. No live provider requests were needed to test this example.

## Limits and costs

- Reply limit: default 1,024 requested tokens; adjustable 64–8,192. Provider/model support ultimately determines enforcement, and reasoning tokens may consume the reply allowance.
- Input limit: default 65,536 UTF-8 bytes; adjustable 1,024–1,048,576. Includes system instructions, up to two previous complete turns, prompt, and attached document. Oversized input is rejected before sending, preserving the prompt.
- Timeout: default 60 seconds; adjustable 5–180. This is a network/socket timeout, not a guaranteed total wall-clock deadline.
- Request limit: default 20 HTTP attempts per editor session (including Refresh models); adjustable 1–1,000. Failed attempts count too. Reset history does not reset the request count. This counter is local to this plugin instance and resets on editor restart.
- Responses are capped at 2 MiB. Empty or incompatible replies produce a notice, with no automatic retries.
- HTTP 429 starts a local pause using a numeric Retry-After value, otherwise 30 seconds. It does not change provider quotas. A date-form Retry-After uses that fallback.
- The provider's reported total tokens are shown after a successful response. The example does not estimate monetary costs or enforce a spending budget. Use provider-side credit/spending controls as well.

A chat subscription is not an API key. Check your provider's API access and billing separately. Hiding the window or exiting the editor cannot undo an already accepted/billable request. The background worker uses no Tk calls; hiding the window leaves a request running, and cleanup ignores results after editor exit.

## Documents and replies

Use **+ Files** in the composer to select up to four UTF-8 text/code files. Each file appears as a removable × chip; chips wrap in narrow windows. Duplicate paths are ignored. Contents are captured when attached, so later disk edits are not silently sent. To refresh a snapshot, remove and reattach it. Files persist across messages and reopening the chat until removed or the editor exits. Attachment contents are not saved in settings.

The file reader is bounded by the configured input-byte limit. Binary files, PDFs, images, invalid UTF-8, oversized files, and selections exceeding the four-file cap are rejected while keeping the existing attachments. At Send, all file headers, contents, optional active-tab snapshot, prompt, system instructions, and history must fit the shared input budget. Rejected requests keep the prompt and attachments unchanged. Attachments never send merely because they are selected.

Removing a chip stops adding that file to new messages. Content sent in an earlier turn can still be present in conversation history; use **Reset context** to clear that history. The visible transcript lists attachment names rather than their full contents, so Copy conversation does not copy hidden file bodies. Previously sent context may still influence replies.

Attach current tab is off by default. When checked, it takes a snapshot of the active tab at Send, including unsaved edits. No unselected files are read or attached automatically. The transcript marks an attachment without copying its full contents into the visible conversation. Only successful turns are kept in memory, separated by endpoint/model/mode. Reset history clears model context but keeps the visible transcript and request counter.

Open reply in new tab copies the latest successful text into a new unsaved document. It does not execute the reply or install it as a plugin. Save and test generated source before loading it.

## Make your own version using the existing AI tool

1. Open the example source and `PLUGIN_DEVELOPMENT.md`.
2. In your existing AI Chat/Code, attach the source and documentation explicitly. Its smaller input limit may require attaching relevant sections or increasing its limits through a separate change.
3. Use [BUILD_PROMPT.md](BUILD_PROMPT.md) as the request. Never attach actual API keys.
4. Save the generated plugin under a new filename such as `my_custom_ai.py`. Change its menu label and custom event namespace so both versions can coexist.
5. Use Plugin Manager to load it, inspect its services/errors, and disable versions you no longer need. Restart after editing an already-loaded plugin.

Customization points:
- `SYSTEM_PROMPTS`: Chat and Code behavior.
- `DEFAULTS` and `validate_config`: defaults and local limits.
- `build_messages`: context and history policy.
- `send_request` / `request_json`: Responses or Chat Completions payloads, reply parsing, and authentication.
- `list_models`: account model-catalog discovery; it does not guarantee a model supports the selected API or has generation permissions.
- `ExampleAI.show`: interface and controls.
- `worker` / `poll`: background work, error handling, and GUI updates.

## Plugin communication

Other plugins can call `api.call_service("my_ai_example.get_status")` to read `busy`, `requests_used`, `request_limit`, and `cooldown_seconds`.

Subscribe with `api.on("my_ai_example.reply", callback)`. A successful reply event contains `source`, `text`, and `model`. Event delivery is queued on the GUI thread. This is a deliberate text-sharing integration with loaded plugins, so consider attached content when choosing which plugins to load. Copying this example under a new filename changes the qualified service prefix automatically; update the custom event name yourself.

## Test without spending API credits

```sh
python3 -B -m unittest -v test_my_ai_example
```

Tests use mocked HTTP responses and never contact a real provider. Real provider compatibility, account permissions, model behavior, and billing are not verified by the offline tests.
