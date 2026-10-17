"""Minimal client for OpenAI-compatible chat APIs (Groq, Gemini, OpenRouter, Ollama,
OpenAI, xAI). Claude goes through the official Anthropic SDK instead (claude.py).

Uses only the standard library. Free tiers rate-limit aggressively, so 429s are
retried with the server's Retry-After hint.

Messages may carry internal keys starting with "_" (e.g. a provider's raw reply);
they are stripped before anything is sent.
"""

import http.client
import json
import re
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class ToolCallError(LLMError):
    """The model produced a malformed tool call or unparseable output (common with open models)."""


class RequestTooLarge(LLMError):
    """The request exceeds the provider's per-minute token limit (free tiers are small)."""


def from_settings(s):
    """Build the right client for resolved provider settings (see config.llm_settings)."""
    if not s.get("model"):
        raise LLMError(f"No model chosen for {s['label']}. Run `tm setup` or `tm models` to pick one.")
    if s["kind"] == "anthropic":
        from taskmind.claude import ClaudeLLM
        return ClaudeLLM(s["model"], s.get("api_key"), context_budget=s["budget"])
    return LLM(
        s["base_url"], s["model"], s.get("api_key"),
        context_budget=s["budget"],
        max_tokens_field=s.get("max_tokens_field", "max_tokens"),
        send_temperature=s.get("temperature", True),
        # Only Groq's free tier needs tiny replies reserved; reasoning models elsewhere
        # spend part of max_tokens on thinking, so give them room.
        min_reply_tokens=0 if s["provider"] == "groq" else 8000,
    )


class LLM:
    def __init__(self, base_url, model, api_key=None, timeout=120, max_retries=4, context_budget=9000,
                 max_tokens_field="max_tokens", send_temperature=True, min_reply_tokens=0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.context_budget = context_budget
        self.max_tokens_field = max_tokens_field
        self.send_temperature = send_temperature
        self.min_reply_tokens = min_reply_tokens

    def list_models(self) -> list:
        data = self._request("/models")
        return sorted(m["id"] for m in data.get("data", []) if m.get("id"))

    def chat(self, messages, tools=None, json_mode=False, temperature=0.2, max_tokens=2048,
             tool_choice="auto") -> dict:
        """Send a chat request and return the assistant message dict."""
        body = {
            "model": self.model,
            "messages": [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages],
            self.max_tokens_field: max(max_tokens, self.min_reply_tokens),
        }
        if self.send_temperature:
            body["temperature"] = temperature
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._request("/chat/completions", body)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response from model: {str(data)[:300]}") from exc

    def _request(self, path, body=None) -> dict:
        """POST body as JSON, or GET when body is None."""
        # Cloudflare in front of some providers (Groq) blocks Python's default User-Agent.
        headers = {"Content-Type": "application/json", "User-Agent": "taskmind/0.1"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = json.dumps(body).encode() if body is not None else None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.base_url + path, data=payload, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                try:
                    return json.loads(raw)
                except ValueError as exc:  # e.g. an HTML error page from a proxy
                    raise LLMError(f"{self.base_url} sent a reply that isn't JSON: {raw[:120]!r}") from exc
            except urllib.error.HTTPError as err:
                detail = err.read().decode(errors="replace")
                daily = err.code == 429 and _is_daily_limit(detail)
                retryable = (err.code == 429 and not daily) or err.code >= 500
                if retryable and attempt < self.max_retries:
                    time.sleep(_retry_delay(err.headers.get("retry-after"), detail, attempt))
                    continue
                if err.code == 413:
                    raise RequestTooLarge(detail[:500]) from err
                if err.code == 400 and ("tool_use_failed" in detail or "output_parse_failed" in detail):
                    raise ToolCallError(detail[:500]) from err
                if err.code == 404 and "model" in detail.lower():
                    raise LLMError(f"Model '{self.model}' is not available here. Run `tm models` to see "
                                   "the current ones, then `tm config --model <id>`.") from err
                if err.code == 401:
                    raise LLMError("The API key was rejected. Run `tm setup` to enter a new one, then `tm doctor` to check it.") from err
                if err.code == 403:
                    raise LLMError(f"Access denied by the provider (HTTP 403): {detail[:300]}") from err
                if daily:
                    raise LLMError(
                        f"Daily free-tier limit reached for {self.model}. Each model has its own daily "
                        "allowance, so you can switch now, e.g. `tm config --model openai/gpt-oss-20b`, "
                        "or wait for it to reset.\n" + _wait_hint(detail)
                    ) from err
                if err.code == 429:
                    raise LLMError(
                        "Free-tier rate limit reached. Wait a bit, or switch to a smaller model "
                        "with `tm config --model ...`.\n" + detail[:300]
                    ) from err
                raise LLMError(f"HTTP {err.code}: {detail[:500]}") from err
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as err:
                # Network trouble (incl. slow replies timing out): retry, then fail over.
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"Could not reach {self.base_url}: {getattr(err, 'reason', err)}") from err
        raise LLMError("Request failed after retries")


def _is_daily_limit(detail) -> bool:
    """Per-day caps (TPD/RPD) won't clear in seconds, so retrying only wastes time."""
    return bool(re.search(r"\((TPD|RPD)\)|per day", detail))


def _wait_hint(detail) -> str:
    match = re.search(r"try again in ([\dhms.]+)", detail)
    return f"The provider says to try again in {match.group(1).rstrip('.')}." if match else ""


def _retry_delay(header, detail, attempt) -> float:
    if header:
        try:
            return min(float(header), 60)
        except ValueError:
            pass
    # Groq puts the wait in the message, e.g. "Please try again in 7.5s".
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", detail)
    if match:
        return min(int(match.group(1) or 0) * 60 + float(match.group(2)) + 0.5, 60)
    return min(2 ** attempt * 2, 30)


def extract_json(text):
    """Parse JSON from a model reply, tolerating code fences and surrounding prose."""
    if text is None:
        raise ValueError("Empty reply")
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


NO_TOOLS = "You have no tools in this step. Do not call any tool; reply with only the JSON object."


def chat_without_tools(llm, messages, **kw) -> dict:
    """Chat with no tools offered. Models that have seen tools earlier sometimes try to
    call one anyway (the provider rejects that), so remind them once and retry."""
    try:
        return llm.chat(messages, **kw)
    except ToolCallError:
        return llm.chat(messages + [{"role": "user", "content": NO_TOOLS}], **kw)


def extract_object(text) -> dict:
    """Like extract_json, but the reply must be a JSON object (models sometimes send a list)."""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def ask_json(llm, system, user, max_tokens=3000) -> dict:
    """Ask for a JSON object, retrying once if the reply does not parse."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    reply = chat_without_tools(llm, messages, json_mode=True, max_tokens=max_tokens)
    try:
        return extract_object(reply.get("content"))
    except ValueError:
        if reply.get("content"):  # an empty assistant turn is invalid on some APIs (Claude)
            messages.append({"role": "assistant", "content": reply["content"]})
        messages.append({"role": "user", "content": "That was not a valid JSON object. Reply with only the JSON object."})
        reply = chat_without_tools(llm, messages, json_mode=True, max_tokens=max_tokens)
        try:
            return extract_object(reply.get("content"))
        except ValueError as exc:
            raise LLMError("The model did not return valid JSON. Try again or use another model.") from exc
