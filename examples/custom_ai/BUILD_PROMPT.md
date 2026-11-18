Use the attached Pocket Editor plugin example and the actual PluginAPI documentation to build my own AI Chat/Code plugin.

Create one Python file named my_custom_ai.py, with register(api) and menu command “My Custom AI…”. Keep configurable OpenAI Responses and compatible Chat Completions formats, provider presets, model refresh, and exact model ID selection. Preserve the different token-limit and response-parsing fields for each format. Retain Chat/Code modes, explicit active-tab attachment, independent conversation histories, background requests, and opening replies in new unsaved tabs.

Do not embed credentials. Keep API keys in memory or an environment variable; exclude them from saved settings, history, and error messages. Make only explicit user-triggered requests. Keep reply-token, input-byte, network-timeout, and per-session request limits visible and validated. Explain that request/token caps do not enforce a monetary budget, and do not add automatic retries or fallback providers.

Use ttk styles and the editor's fonts. All Tk and PluginAPI calls belong on the GUI thread. Clean up timers/windows and ignore worker results after shutdown. Preserve current files and save prompts.

Change the settings filename, menu label, and custom reply event namespace to my_custom_ai. Publish a get_status service and a my_custom_ai.reply event. Do not invent new editor APIs or modify the bundled AI backend.

Return complete source, setup steps, provider limitations, and offline tests using mocked network responses. Do not claim tests were run unless you actually ran them. Ask me which native provider API to support before changing the transport to another API format.
