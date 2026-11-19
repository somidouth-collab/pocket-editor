# Pocket Editor — 2026.11.19.post1

Created by **Somi Douth** · somidouth@gmail.com

- Added **Codex Assistant** with installed-CLI discovery, terminal login/setup guidance, project selection, optional tab snapshots, read-only defaults and explicit workspace edits.
- Added Stop/process-tree cleanup, bounded prompts/replies, and a five-minute task timeout. Requests use the user's existing Codex CLI account/configuration. No model request is sent automatically.
- Added a reusable blue/violet orbit loading animation with independent job tokens, optional embedded placement and progress updates. Codex, AI model/reply requests and Git operations use it.
- Added in-editor Codex help, offline HTML guides and a shared loading-service example for plugin authors.

Offline tests use a fake CLI and do not consume Codex credits. Full Codex account/model behavior depends on the user's CLI installation, login, configuration and platform sandbox. This release retains the Gemini and consolidated language-plugin fixes from 2026.11.19. License remains undecided.
