# AI Assistant

AI Assistant is the single Chat/Code plugin. It replaces the old AI Chat/AI Code frontend and the former My AI Example. Open **Extensions → AI Assistant…**, then choose **Settings**. **How to use** opens this guide inside the chat window.

## Google Gemini: first working conversation

1. Open https://aistudio.google.com/apikey in your browser and create a Gemini Developer API key for your Google project.
2. In AI Assistant Settings, select **Google Gemini**. The plugin fills **API format: Google Gemini** and **API endpoint: https://generativelanguage.googleapis.com/v1beta**.
3. Paste your Gemini key into **API key**. Use a Gemini key, not your OpenAI key or Google account password. Alternatively set the `GEMINI_API_KEY` environment variable before starting the editor.
4. Click **Refresh models**. Choose a currently available text-generation model from the list. The catalog only shows models that advertise `generateContent`; model access and quotas still depend on your Google project. You can also enter an exact model ID. A copied `models/` prefix is accepted.
5. Keep **Reasoning** at **default**. That setting only applies to OpenAI Responses. Start with **Reply tokens: 1024**, and raise it explicitly if a thinking model returns no visible text. Refreshing models makes a catalog request but does not generate a reply.
6. Click **Save settings**, then **Done**. Select **Chat**, enter `Say hello in one short sentence`, and click **Send**.
7. For code help, select **Code**, attach a small `.py` or `.php` file with **+ Files**, and ask `Explain this file and suggest one improvement`.

The plugin constructs `/models/MODEL:generateContent` itself and sends the key in the `x-goog-api-key` header. Do not append `/chat/completions`, paste a model URL into Model ID, or add `?key=...` to the endpoint. Gemini uses native Google request/response fields rather than OpenAI messages.

## Provider presets

| Provider | API format | API key environment variable | Setup |
| --- | --- | --- | --- |
| Google Gemini | Google Gemini | GEMINI_API_KEY | Create a key in Google AI Studio; refresh text models |
| OpenAI | OpenAI Responses | OPENAI_API_KEY | Use OpenAI Platform API access and a Responses-compatible text model |
| OpenRouter | Chat Completions | OPENROUTER_API_KEY | Use an OpenRouter key and the full provider/model ID |
| Groq | Chat Completions | GROQ_API_KEY | Use a Groq console key and an available text model |
| DeepSeek | Chat Completions | DEEPSEEK_API_KEY | Use a DeepSeek platform key and current model ID |
| Ollama | Chat Completions | OLLAMA_API_KEY, optional for local use | Start local Ollama and download a model first |
| Custom | Choose a supported format | CUSTOM_AI_API_KEY | Supply a matching full API endpoint and model ID |

OpenAI-compatible describes the API format; it does not mean you must use an OpenAI account. Each hosted provider requires its own credentials. Native Claude/Anthropic and other unsupported API formats cannot be used simply by changing the URL.

### Local Ollama example

Install Ollama from https://ollama.com/download and start it. Download a model using `ollama pull MODEL`, replacing MODEL with an available model suitable for your computer. In AI Assistant select **Ollama**, keep `http://127.0.0.1:11434/v1/chat/completions`, leave the key empty for a standard local server, and click **Refresh models**. Select the model you downloaded, Save settings, and Send a short message. If no models are listed, download one first. An Ollama installation on another computer needs an appropriately secured reachable endpoint; this plugin permits plain HTTP only on loopback.

## Settings, privacy, and limits

Non-secret settings are stored in `pocket_editor_plugins/ai_assistant_settings.json`. The old My AI Example settings are read as a migration fallback. API keys stay in memory or are read from your environment; they are never written to settings. Switching presets keeps each provider's in-memory key separate. Editing the endpoint to a different host clears the key. Re-enter a key after restarting unless you set its environment variable.

**Requests/session** limits attempts in this editor session, including model-list requests. It does not track your provider's credit balance or Codex/ChatGPT usage. Reaching the cap blocks new requests until you explicitly raise it. HTTP 429 pauses sending; there are no automatic paid retries or continuations. A failed message is restored to the composer when it is still empty, so you can fix settings before retrying. Reset context clears model history, not the request counter or visible transcript.

Attach up to four UTF-8 text/code snapshots with **+ Files**. Binary files, PDFs, images, audio and video are not supported in this text AI plugin. Files are sent only when you click Send; attachments persist until removed. **Attach current tab** is off by default and includes unsaved edits when enabled. The shared input-byte limit covers prompt, system instructions, history and attachments. Remove a file and Reset context to stop carrying previously sent contents forward. **Open reply** creates a new unsaved document and does not execute generated code.

## Troubleshooting

- **HTTP 400:** check API format and endpoint. Gemini requires its native preset and a Developer API key. Some models reject particular output/reasoning settings.
- **HTTP 401/403:** check the provider-specific key, project/model permissions, region and API enablement. A website subscription or chat login is not an API key.
- **HTTP 404:** refresh models; model IDs and endpoint paths can change. A listed model still needs generation access.
- **HTTP 429:** check the provider console's rate limits, quota and billing. Waiting may help a rate limit but does not add exhausted credits.
- **Cannot reach provider:** check internet/proxy/firewall. For Ollama, start its server on the same PC and pull a model.
- **No visible text:** a safety block or thinking-token budget may prevent a text reply. Choose another text model or explicitly raise Reply tokens. No retry is sent automatically.
- **Only OpenAI worked before:** the old example only understood OpenAI-compatible formats. Select the new Google Gemini preset instead of forcing Gemini through Chat Completions.

Offline tests validate request construction, key headers, catalog filtering, history and reply parsing. A live Gemini account request is not included in those tests.

## Official provider references

- Gemini generation: https://ai.google.dev/api/generate-content
- Gemini model catalog: https://ai.google.dev/api/models
- Ollama compatibility: https://docs.ollama.com/api/openai-compatibility
- OpenRouter API: https://openrouter.ai/docs/api_reference/overview
- Groq compatibility: https://console.groq.com/docs/openai
- DeepSeek API: https://api-docs.deepseek.com/
