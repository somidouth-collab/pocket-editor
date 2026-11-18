#!/usr/bin/env python3
"""Ask multiple AI models to write, review, and debug code."""
from __future__ import annotations

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from getpass import getpass
from http.client import HTTPException
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CODING = True
DESCRIPTION = 'Ask multiple AI models to write, review, and debug code.'
SYSTEM_PROMPT = "You are a software engineering assistant. Follow the user's requirements. When editing supplied code, preserve relevant behavior and show a patch or replacement code as requested. Explain bugs, edge cases, and how to verify changes when relevant. State assumptions and never claim to have run commands or tests you did not run."


@dataclass(frozen=True)
class Provider:
    name: str
    key_env: str
    url: str
    key_link: str = ""
    models: tuple[str, ...] = ()
    plan: str = ""


OPENROUTER = Provider(
    "OpenRouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
    "https://openrouter.ai/settings/keys",
)
GEMINI = Provider(
    "Gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta",
    "https://aistudio.google.com/apikey", ("gemini-3.5-flash-lite", "gemini-3.8-flash"),
    "Google AI Studio Free tier project",
)
NVIDIA = Provider(
    "NVIDIA NIM", "NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1",
    "https://build.nvidia.com/explore",
    ("deepseek-ai/deepseek-v4.1-flash", "minimaxai/minimax-m3.1-flash",
     "poolside/laguna-xs-2.1"),
)
GROQ = Provider(
    "GroqCloud", "GROQ_API_KEY", "https://api.groq.com/openai/v1",
    "https://console.groq.com/keys",
    ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"),
    "GroqCloud Free plan",
)
CLOUDFLARE = Provider(
    "Cloudflare", "CLOUDFLARE_API_TOKEN",
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
    "https://dash.cloudflare.com/?to=/:account/ai/workers-ai",
    ("@cf/qwen/qwen3.8-27b",), "Cloudflare Workers Free plan",
)
PROVIDERS = (OPENROUTER, GEMINI, NVIDIA, GROQ, CLOUDFLARE)
OLLAMA = Provider("Ollama (local)", "", "http://127.0.0.1:11434")
OLLAMA_LINK = "https://ollama.com/download/linux"


def command_help():
    lines = [
        "Model menu",
        "  N           Select one model by its number, e.g. 3.",
        "  N,M         Send the same prompt to several models, e.g. 2,5.",
        "  all         Send to every available model. Each request uses that provider's quota.",
        "  /team       Choose a lead and ordered workers for one task and one final answer.",
        "  /list       Show the current model numbers and names.",
        "  /keys       Open provider setup. Provider numbers differ from model numbers.",
        "  /reset      Clear every model's conversation history. Saved keys are kept.",
        "  /quit       Exit. Conversation history is kept only during this run.",
        "  /help       Show this guide.",
        "",
        "Text prompt",
        "  /paste      Type /paste or /past on its own, then paste your text.",
        "              Finish with .end on a line by itself.",
        "  /file PATH  Attach a UTF-8 text or source file to your next message.",
        "              Repeat /file for multiple files. Then type your question and press Enter.",
        "              Example: /file ~/Downloads/chat_ai(5).py",
        "  /help       Show this guide and return to the text prompt.",
        "  Enter       Submit text. An empty line returns to the model menu and clears pending files.",
        "",
        "Team mode (/team)",
        "  Lead        Enter one model number.",
        "  Workers     Enter model numbers in order, e.g. 2,18,22, or all for menu order.",
        "              Use one model per developer, even across different API providers.",
        "  Task        Enter one task. /file and /paste work here too.",
        "              The lead plans, workers contribute in order, and the lead writes the answer.",
        "              One run makes two lead requests and one request per worker (one retry on HTTP 413).",
        "              The task and attachments go to all chosen models. Solo histories stay separate.",
        "",
        "Provider setup (/keys)",
        "  N           Add or replace a key using the provider number. Enter cancels key entry.",
        "  use N       Enable a saved key after confirming its provider is on a Free plan.",
        "  remove N    Delete the locally saved key. Environment variables still apply.",
        "  /back       Return to the model menu and refresh available models.",
        "  /help       Show this guide.",
        "",
        "Setup notes",
        "  Example: /keys, use 2, yes, /back enables an already saved Gemini Free-plan key.",
        "  Cloudflare needs a token and a 32-character account ID.",
        "  Both scripts share keys and Free plan confirmations. Ollama uses local models.",
        "  Environment variables take precedence over saved keys.",
        "",
        "Launch options",
        "  -h, --help  Show this guide and exit.",
        "  --env-only  Ignore saved keys and plan confirmations. Use environment variables or",
        "              local Ollama. Providers that need a saved plan confirmation stay disabled.",
    ]
    if CODING:
        lines.extend([
            "  --file PATH Attach a UTF-8 source file. Repeat for multiple files.",
            "              Files go to each solo model on its first request and to each team run.",
            "              Team mode checks attached Python files for syntax errors locally.",
            "              Example: python3 code_ai.py --file main.py --file utils.py",
        ])
    return "\n".join(lines)


def credentials_path(provider):
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not config_home.is_absolute():
        config_home = Path.home() / ".config"
    return config_home / "multi-ai" / f"{provider.key_env.lower()}.json"


def read_credentials(provider):
    path = credentials_path(provider)
    try:
        with path.open(encoding="utf-8") as file:
            credentials = json.load(file)
        if not isinstance(credentials, dict) or not isinstance(credentials.get("key"), str):
            raise ValueError
        if not isinstance(credentials.get("account_id", ""), str):
            raise ValueError
        return credentials
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        print(f"Could not read saved credentials for {provider.name}. Use /keys to replace them.")
        return {}


def save_credentials(provider, credentials):
    path = credentials_path(provider)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, mode="w", encoding="utf-8", delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(credentials, file)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def show_key_links(saved):
    print("\nFree models and free-tier providers")
    for number, provider in enumerate(PROVIDERS, 1):
        credentials = saved.get(provider, {})
        key, account = effective_credentials(provider, credentials)
        status = "ready" if key else "no key"
        if key and provider.plan and not confirmed_plan(credentials, key, account):
            status = f"confirm Free plan with /keys, then use {number}"
        print(f"  {number}. {provider.name} [{status}]  {provider.key_link}")
    print(f"  Ollama (local) [no key required]  {OLLAMA_LINK}")


def load_credentials(env_only):
    return {} if env_only else {provider: read_credentials(provider) for provider in PROVIDERS}


def effective_credentials(provider, saved):
    key = os.environ.get(provider.key_env, "").strip() or saved.get("key", "")
    account = ""
    if provider == CLOUDFLARE:
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip() or saved.get("account_id", "")
    return key, account


def confirmed_plan(saved, key, account):
    return (saved.get("free_plan") is True and saved.get("key") == key
            and saved.get("account_id", "") == account)


def manage_keys(saved):
    show_key_links(saved)
    while True:
        action = input("\nProvider number to save/change, 'use N' to confirm Free plan, "
                       "'remove N', /help, or /back: ").strip().lower()
        if action in ("/back", ""):
            return
        if action == "/help":
            print(command_help())
            continue
        command, _, number = action.rpartition(" ")
        if number not in {str(index) for index in range(1, len(PROVIDERS) + 1)}:
            print("Enter a provider number from the setup list.")
            continue
        provider = PROVIDERS[int(number) - 1]
        existing = saved.get(provider, {})
        try:
            if command == "remove":
                credentials_path(provider).unlink(missing_ok=True)
                saved.pop(provider, None)
                print(f"Removed the saved {provider.name} key.")
                continue
            if command == "use":
                key, account = effective_credentials(provider, existing)
                if not provider.plan or not key:
                    print("Choose a provider with a saved key that requires a Free plan check.")
                    continue
                if provider == CLOUDFLARE and not valid_account_id(account):
                    print("Enter the provider number first to save your Cloudflare Account ID.")
                    continue
                if input(f"Is this key on a {provider.plan}? Type yes: ").strip().lower() == "yes":
                    credentials = {"key": key, "account_id": account, "free_plan": True}
                    save_credentials(provider, credentials)
                    saved[provider] = credentials
                    print(f"Enabled {provider.name} for both scripts. Reconfirm if you change plans or keys.")
                continue
            if command:
                print("Use a provider number, 'use N', or 'remove N'.")
                continue
            print(f"Get your {provider.name} key: {provider.key_link}")
            key = getpass("Paste API key (Enter cancels): ").strip()
            if not key:
                continue
            account = ""
            if provider == CLOUDFLARE:
                account = input("Cloudflare Account ID (Enter keeps saved ID): ").strip()
                account = account or effective_credentials(provider, existing)[1]
                if not valid_account_id(account):
                    print("Enter a 32-character Cloudflare Account ID.")
                    continue
            credentials = {"key": key, "account_id": account}
            if provider == GEMINI:
                request_api(provider, key, "/models?pageSize=1", timeout=15)
            save_credentials(provider, credentials)
            saved[provider] = credentials
            print(f"Saved the {provider.name} key for both scripts.")
            if provider.plan:
                print(f"Use 'use {number}' after checking the provider's plan.")
            if os.environ.get(provider.key_env, "").strip():
                print(f"{provider.key_env} is set and takes precedence for this session.")
        except APIError as error:
            print(f"{provider.name} key check failed: {error}")
        except OSError:
            print(f"Could not update the saved {provider.name} key.")


def valid_account_id(account):
    return isinstance(account, str) and re.fullmatch(r"[0-9a-fA-F]{32}", account) is not None


@dataclass(frozen=True)
class Model:
    provider: Provider
    name: str

    @property
    def label(self):
        plan = " [Free plan]" if self.provider.plan else ""
        return f"{self.provider.name} / {self.name}{plan}"


class APIError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def read_nvidia_stream(response):
    parts = []
    finish_reason = None
    for line in response:
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            break
        event = json.loads(data)
        if event.get("error"):
            raise APIError("NVIDIA reported a streaming error. Try again.")
        for choice in event.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if isinstance(content, str):
                parts.append(content)
            finish_reason = choice.get("finish_reason") or finish_reason
    else:
        if not finish_reason:
            raise APIError("NVIDIA ended the stream before completing the reply.")
    if finish_reason == "length" and parts:
        parts.append("\n\n[Reply stopped at NVIDIA's output limit.]")
    return "".join(parts)


def gemini_parts(candidate):
    return "".join(
        part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
        if not part.get("thought")
    )


def read_gemini_stream(response):
    parts = []
    finish_reason = None
    for line in response:
        if not line.startswith(b"data:"):
            continue
        event = json.loads(line[5:].strip())
        if event.get("error"):
            raise APIError("Gemini reported an error while streaming.")
        candidates = event.get("candidates", [])
        if candidates:
            parts.append(gemini_parts(candidates[0]))
            finish_reason = candidates[0].get("finishReason") or finish_reason
    if not finish_reason and any(parts):
        raise APIError("Gemini ended the stream before completing the reply.")
    return "".join(parts)


def request_api(provider, key, path, payload=None, timeout=90, stream=False):
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
        "User-Agent": "multi-ai-cli/1.0",
    }
    if key:
        if provider == GEMINI:
            headers["x-goog-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    request = Request(
        provider.url + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if stream:
                if provider == GEMINI:
                    return read_gemini_stream(response)
                return read_nvidia_stream(response)
            result = json.load(response)
        if not isinstance(result, dict):
            raise APIError("Unexpected API response.")
        if result.get("error"):
            error = result["error"]
            detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise APIError(detail)
        return result
    except HTTPError as error:
        detail = error.reason
        code = None
        try:
            info = json.load(error).get("error", {})
            detail = info.get("message") or detail
            code = info.get("code")
        except (ValueError, AttributeError):
            pass
        detail = str(detail).replace(key, "[redacted]") if key else str(detail)
        raise APIError(f"HTTP {error.code}: {detail}", status=error.code, code=code) from error
    except TimeoutError as error:
        raise APIError(f"{provider.name} did not send a reply in time. Try again or select another model.") from error
    except (URLError, OSError, HTTPException) as error:
        raise APIError(f"Connection failed: {error}") from error
    except (ValueError, TypeError, AttributeError) as error:
        raise APIError("Invalid response data returned by provider.") from error


def is_free_openrouter_model(entry):
    if not isinstance(entry, dict):
        return False
    name = entry.get("id")
    architecture = entry.get("architecture") or {}
    pricing = entry.get("pricing") or {}
    if not isinstance(architecture, dict) or not isinstance(pricing, dict):
        return False
    if not isinstance(name, str) or not name.endswith(":free"):
        return False
    if name == "thinkingmachines/inkling-2-small:free":
        return False  # OpenRouter restricts this variant to agentic harnesses.
    if "text" not in (architecture.get("input_modalities") or []):
        return False
    if not {"prompt", "completion"}.issubset(pricing):
        return False
    try:
        return all(Decimal(str(price)) == 0 for price in pricing.values())
    except (InvalidOperation, ValueError):
        return False


def list_models(provider, key):
    if provider.models:
        return provider.models
    if provider == OLLAMA:
        result = request_api(provider, key, "/api/tags", timeout=3)
        local_models = []
        for entry in result.get("models", []):
            name = entry.get("name") or entry.get("model")
            if (not name or entry.get("remote_host") or entry.get("remote_model")
                    or name.endswith((":cloud", "-cloud"))):
                continue
            try:
                details = request_api(provider, key, "/api/show", {"model": name}, timeout=5)
            except APIError as error:
                print(f"Skipped Ollama model {name}: {error}")
                continue
            if details.get("remote_host") or details.get("remote_model"):
                continue
            capabilities = details.get("capabilities", [])
            if not capabilities or "completion" in capabilities:
                local_models.append(name)
        return sorted(set(local_models))

    result = request_api(OPENROUTER, key, "/models?output_modalities=text", timeout=15)
    data = result.get("data")
    if not isinstance(data, list):
        raise APIError("Unexpected model catalog.")
    return sorted({entry["id"] for entry in data if is_free_openrouter_model(entry)})


def ask(model, key, history, prompt, max_output_tokens=None):
    provider = model.provider
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": prompt},
    ]
    payload = {"model": model.name, "messages": messages, "stream": False}
    if max_output_tokens is not None:
        payload["max_tokens"] = max_output_tokens
    if provider != OLLAMA and model.name not in provider.models:
        if provider != OPENROUTER or not model.name.endswith(":free"):
            raise APIError("This model is outside the free model list.")
    try:
        if provider == OLLAMA:
            if max_output_tokens is not None:
                payload.pop("max_tokens", None)
                payload["options"] = {"num_predict": max_output_tokens}
            result = request_api(provider, "", "/api/chat", payload, timeout=180)
            content = result["message"]["content"]
        elif provider == GEMINI:
            payload = {
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [
                    {"role": "model" if message["role"] == "assistant" else "user",
                     "parts": [{"text": message["content"]}]}
                    for message in messages[1:]
                ],
            }
            if max_output_tokens is not None:
                payload["generationConfig"] = {"maxOutputTokens": max_output_tokens}
            if model.name == "gemini-3.8-flash":
                path = f"/models/{model.name}:streamGenerateContent?alt=sse"
                content = request_api(provider, key, path, payload, timeout=120, stream=True)
            else:
                result = request_api(provider, key, f"/models/{model.name}:generateContent", payload)
                content = gemini_parts(result["candidates"][0])
        elif provider == NVIDIA and model.name == NVIDIA.models[0]:
            payload.update(stream=True, max_tokens=max_output_tokens or 8192)
            content = request_api(provider, key, "/chat/completions", payload, timeout=120, stream=True)
        else:
            result = request_api(provider, key, "/chat/completions", payload)
            content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise APIError("The provider did not return a text reply.") from error
    if not isinstance(content, str) or not content.strip():
        raise APIError("The model returned an empty reply.")
    return content.strip()


def configure(saved):
    keys = {}
    models = []
    for provider in PROVIDERS:
        credentials = saved.get(provider, {})
        key, account = effective_credentials(provider, credentials)
        if not key or (provider.plan and not confirmed_plan(credentials, key, account)):
            continue
        if provider == CLOUDFLARE:
            if not valid_account_id(account):
                continue
            provider = replace(provider, url=provider.url.format(account_id=account))
        try:
            models.extend(Model(provider, name) for name in list_models(provider, key))
        except APIError as error:
            print(f"{provider.name} catalog unavailable: {error}")
        keys[provider] = key

    try:
        models.extend(Model(OLLAMA, name) for name in list_models(OLLAMA, ""))
    except APIError:
        pass
    return models, keys


def select_models(selection, models):
    if selection == "all":
        return models
    try:
        numbers = [int(part) for part in selection.split(",")]
    except ValueError as error:
        raise ValueError("Enter a model number, comma-separated numbers, or all.") from error
    if any(number < 1 or number > len(models) for number in numbers):
        raise ValueError(f"Choose model numbers from 1 to {len(models)}.")
    return [models[number - 1] for number in dict.fromkeys(numbers)]


def model_developer(model):
    if model.provider == GEMINI:
        return "google"
    name = model.name.lower().removeprefix("@cf/")
    publisher, separator, _ = name.partition("/")
    if not separator:
        # Local model tags may lack a publisher. Recognize common model families.
        for family, developer in (
            ("gemma", "google"), ("qwen", "qwen"), ("llama", "meta"),
            ("gpt-oss", "openai"), ("deepseek", "deepseek"), ("nemotron", "nvidia"),
            ("minimax", "minimax"), ("mistral", "mistralai"),
        ):
            if name.startswith(family):
                return developer
        return "ollama" if model.provider == OLLAMA else model.provider.name.lower()
    return {"deepseek-ai": "deepseek", "minimaxai": "minimax", "meta-llama": "meta"}.get(
        publisher, publisher
    )


def show_models(models, show_developers=False):
    if not models:
        print("\nNo models available. Type /keys to set up a provider, or start local Ollama.")
        return
    print("\nAvailable free models")
    for number, model in enumerate(models, 1):
        developer = f" ({model_developer(model)})" if show_developers else ""
        print(f"  {number}. {model.label}{developer}")


def read_attachment(path):
    path = path.expanduser()
    if not path.is_file():
        raise OSError(f"Not a regular file: {path}")
    return f"File: {path.name}\n{path.read_text(encoding='utf-8')}"


def read_prompt():
    attachments = []
    while True:
        prompt = input("Your text (/paste, /file PATH, /help): ").strip()
        if not prompt:
            return "", []
        command, *arguments = prompt.split(maxsplit=1)
        command = command.lower()
        if command == "/help" and not arguments:
            print(command_help())
            continue
        if command == "/file":
            if not arguments:
                print("Usage: /file path/to/file.py")
                continue
            filename = arguments[0]
            if len(filename) > 1 and filename[0] in ("'", '"') and filename[-1] == filename[0]:
                filename = filename[1:-1]
            try:
                path = Path(filename).expanduser()
                attachments.append(read_attachment(path))
            except (OSError, UnicodeError, RuntimeError, ValueError) as error:
                print(f"Could not attach file: {error}")
            else:
                print(f"Attached {path.name}. It will be sent to the selected models with your next message.")
            continue
        if command not in ("/paste", "/past"):
            return prompt, attachments
        if not arguments:
            break
        print("Type /paste or /past on its own. To attach a file here, use /file PATH.")
    print("Paste your text, then enter .end on its own line.")
    lines = []
    while True:
        line = input()
        if line == ".end":
            return "\n".join(lines).strip(), attachments
        lines.append(line)


def send_prompt(selected, keys, histories, prompt, file_context, attachments=()):
    if not selected:
        return
    with ThreadPoolExecutor(max_workers=min(4, len(selected))) as pool:
        requests = {}
        for model in selected:
            history = histories.get(model, [])
            parts = [prompt]
            if not history and file_context:
                parts.append(file_context)
            parts.extend(attachments)
            text = "\n\n".join(parts)
            future = pool.submit(ask, model, keys.get(model.provider, ""), history, text)
            requests[future] = (model, text)
        for future in as_completed(requests):
            model, text = requests[future]
            print(f"\n--- {model.label} ---")
            try:
                answer = future.result()
            except APIError as error:
                print(f"Request failed: {error}", flush=True)
                if model.provider == GROQ and error.status == 403:
                    if error.code == "model_permission_blocked_org":
                        print("Groq reports an organization model restriction.")
                    elif error.code == "model_permission_blocked_project":
                        print("Groq reports a project model restriction.")
                    else:
                        print("Groq provided no specific permission error code. "
                              "Check the key's project and organization access.")
                    print("Groq model permissions: https://console.groq.com/docs/model-permissions")
                continue
            histories.setdefault(model, []).extend([
                {"role": "user", "content": text},
                {"role": "assistant", "content": answer},
            ])
            print(answer, flush=True)


def team_workers(selection, models, lead):
    workers = []
    developers = {model_developer(lead)}
    for model in select_models(selection, models):
        developer = model_developer(model)
        if developer in developers:
            if selection == "all":
                continue
            raise ValueError(f"Choose one model per developer. {developer} is already on the team.")
        developers.add(developer)
        workers.append(model)
    if not workers:
        raise ValueError("Choose at least one worker from a different developer.")
    return workers


def python_attachment_checks(attachments):
    checks = []
    for attachment in attachments:
        header, _, source = attachment.partition("\n")
        filename = header.removeprefix("File: ")
        if Path(filename).suffix.lower() != ".py":
            continue
        try:
            compile(source, filename, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as error:
            checks.append(f"{filename}: syntax error at line {getattr(error, 'lineno', '?')}: {getattr(error, 'msg', str(error))}")
        else:
            checks.append(f"{filename}: syntax check passed")
    return checks


TEAM_PROMPT_BYTES = 16000


def bounded_text(text, limit):
    """Bound UTF-8 bytes, explicitly marking omitted source or reports."""
    if len(text.encode("utf-8")) <= limit:
        return text
    marker = "\n[TRUNCATED: omitted text is unavailable; do not infer its contents.]\n"
    available = max(0, limit - len(marker.encode("utf-8")))
    return text.encode("utf-8")[:available].decode("utf-8", errors="ignore") + marker


def team_context(task, verification, plan="", reports=(), statuses="", budget=TEAM_PROMPT_BYTES):
    # Reserve room for instructions and give source priority over model commentary.
    source = bounded_text(task + verification, budget // 2)
    plan = bounded_text(plan, budget // 10)
    report_budget = budget // 4
    compact = [dict(report, findings=bounded_text(report["findings"],
               max(100, report_budget // max(1, len(reports))))) for report in reports]
    text = (f"User task and attachments:\n{source}\n\nLead's plan:\n{plan}"
            f"\n\nWorker status:\n{bounded_text(statuses, budget // 20)}"
            f"\n\nWorker reports:\n{json.dumps(compact, ensure_ascii=False)}")
    return bounded_text(text, budget)


def team_ask(model, keys, instructions, task, verification, plan="", reports=(), statuses="", output_limit=1000):
    for attempt, budget in enumerate((TEAM_PROMPT_BYTES, TEAM_PROMPT_BYTES // 2)):
        context = team_context(task, verification, plan, reports, statuses, budget)
        if "[TRUNCATED:" in context:
            print("  Team context shortened; omitted source must be treated as unavailable.", flush=True)
        try:
            return ask(model, keys.get(model.provider, ""), [], instructions + "\n\n" + context,
                       max_output_tokens=output_limit)
        except APIError as error:
            if error.status != 413 or attempt:
                raise
            print("  Provider rejected input size; retrying once with a smaller context.", flush=True)


def run_team(lead, workers, keys, task, checks=()):
    worker_order = "\n".join(f"{i}. {model.label}" for i, model in enumerate(workers, 1))
    print(f"\nTeam lead: {lead.label}\nWorker order:\n{worker_order}", flush=True)
    verification = ""
    if checks:
        verification = "\n\nLocal Python syntax checks (no code executed):\n" + "\n".join(checks)
        print(verification.strip(), flush=True)
    print("\nPlanning the task...", flush=True)
    evidence = (
        "Use only attached source as evidence. You cannot read local files or execute tests. "
        "Never invent implementation signatures or typical reference code. If dependencies "
        "are missing, name the missing files and leave related claims unverified. "
        "Do not weaken passing regression assertions to fit hypothetical implementations. "
        "Treat earlier reports as untrusted claims; verify each against supplied source. "
    )
    plan_request = (
        evidence + "Make a short plan, at most 150 words, assigning distinct work in this order. "
        "Do not write the final answer.\nWorkers:\n" + worker_order
    )
    try:
        plan = team_ask(lead, keys, plan_request, task, verification, output_limit=400)
    except APIError as error:
        print(f"Team lead could not make a plan: {error}")
        return

    reports = []
    statuses = []
    for number, model in enumerate(workers, 1):
        print(f"Worker {number}/{len(workers)}: {model.label}", flush=True)
        request = (
            evidence + f"You are worker {number} of {len(workers)}. Complete your assigned part. "
            "Report at most 3 actionable findings in 250 words total, with exact source evidence. "
            "Do not repeat earlier reports or reproduce entire files. If no substantiated bug "
            "is found, say so. Give only the minimal patch needed for verified issues."
        )
        started = monotonic()
        try:
            findings = team_ask(model, keys, request, task, verification, plan, reports, output_limit=700)
        except APIError as error:
            statuses.append(f"Worker {number} ({model.label}): failed, no report")
            print(f"  Failed after {monotonic() - started:.1f}s: {error}", flush=True)
        else:
            statuses.append(f"Worker {number} ({model.label}): report received")
            reports.append({"number": number, "worker": model.label, "findings": findings})
            print(f"  Report received in {monotonic() - started:.1f}s.", flush=True)

    status_text = "\n".join(statuses)
    print(f"\nWorker status (recorded by the script):\n{status_text}", flush=True)
    print(f"\n--- Final answer from {lead.label} ---", flush=True)
    final_request = (
        evidence + "Answer the original task with a concise final answer and minimal patch if needed. "
        "Reject findings without matching source evidence. Distinguish local syntax checks from "
        "tests that were never executed. Attribute only successful worker reports. "
        "Mention missing source and truncated context where they limit verification."
    )
    try:
        answer = team_ask(lead, keys, final_request, task, verification, plan, reports, status_text, output_limit=2000)
    except APIError as error:
        print(f"Team lead could not finish the answer: {error}")
        for report in reports:
            print(f"\n--- Report from {report['worker']} ---\n{report['findings']}")
    else:
        print(answer, flush=True)


def team_menu(models, keys, startup_attachments):
    if len({model_developer(model) for model in models}) < 2:
        print("Team mode needs available models from at least two developers.")
        return
    show_models(models, show_developers=True)
    while True:
        selection = input("\nLead model number (Enter cancels): ").strip()
        if not selection:
            return
        try:
            selected = select_models(selection, models)
            if len(selected) != 1 or not selection.isdecimal():
                raise ValueError("Choose one model number for the lead.")
            lead = selected[0]
        except ValueError as error:
            print(error)
            continue
        break

    while True:
        selection = input("Worker numbers in order, e.g. 2,18,22 (all or Enter to cancel): ").strip().lower()
        if not selection:
            return
        try:
            workers = team_workers(selection, models, lead)
        except ValueError as error:
            print(error)
            continue
        break

    print("Enter the team task. The selected models will receive it and any attached files.")
    prompt, attachments = read_prompt()
    if prompt:
        team_attachments = [*startup_attachments, *attachments]
        task = "\n\n".join([prompt, *team_attachments])
        checks = python_attachment_checks(team_attachments) if CODING else ()
        run_team(lead, workers, keys, task, checks)


def main():
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        epilog=command_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--env-only", action="store_true", help="use environment variables and local Ollama only",
    )
    if CODING:
        parser.add_argument(
            "--file", action="append", type=Path, default=[],
            help="attach a UTF-8 source file to the first request per model (repeatable)",
        )
    args = parser.parse_args()
    files = getattr(args, "file", [])
    try:
        startup_attachments = [read_attachment(path) for path in files]
    except (OSError, UnicodeError) as error:
        parser.error(f"Could not read source file: {error}")
    file_context = "\n\n".join(startup_attachments)
    if files:
        print("Attached files will be sent to the models you select: " +
              ", ".join(path.name for path in files))

    saved = load_credentials(args.env_only)
    show_key_links(saved)
    models, keys = configure(saved)
    if args.env_only and not models:
        print("No models found. Set a provider key in your environment or start local Ollama.")
        return 1
    histories = {}
    show_models(models)
    while True:
        selection = input("\nModels (e.g. 1,3 or all) [/team /help /keys /list /reset /quit]: ").strip().lower()
        if selection == "/quit":
            return 0
        if selection == "/help":
            print(command_help())
            continue
        if selection == "/keys":
            if args.env_only:
                show_key_links({})
                print("Run without --env-only to manage saved credentials.")
            else:
                manage_keys(load_credentials(False))
                models, keys = configure(load_credentials(False))
                show_models(models)
            continue
        if selection == "/list":
            show_models(models)
            continue
        if selection == "/team":
            team_menu(models, keys, startup_attachments)
            continue
        if selection == "/reset":
            histories.clear()
            print("Conversation history cleared.")
            continue
        if not models:
            print("Type /keys to set up a provider.")
            continue
        try:
            selected = select_models(selection, models)
        except ValueError as error:
            print(error)
            continue
        prompt, attachments = read_prompt()
        if prompt:
            send_prompt(selected, keys, histories, prompt, file_context, attachments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        print()
