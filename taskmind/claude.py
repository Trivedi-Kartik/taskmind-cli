"""Claude provider, through the official Anthropic SDK.

The SDK is optional: only people who choose Claude need `pip install anthropic`.
TaskMind's agent speaks the OpenAI chat format, so this module translates both
ways. Claude's reply blocks (including thinking blocks) are kept on the returned
message under "_raw" and sent back unchanged on the next turn, as the API expects.
"""

import json

from taskmind.llm import LLMError, RequestTooLarge, ToolCallError

# Models that accept server-side refusal fallbacks in their simple "default" form.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000  # thinking is part of the reply; small caps would cut it off


def _sdk():
    try:
        import anthropic
    except ImportError as exc:
        raise LLMError("Claude needs Anthropic's official SDK. Install it with: pip install anthropic") from exc
    return anthropic


def to_claude(messages):
    """OpenAI-style messages -> (system text, Claude messages)."""
    system, out = [], []
    for m in messages:
        role = m["role"]
        if role == "system":
            system.append(m.get("content") or "")
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m.get("content") or ""}
            last = out[-1] if out else None
            # All results for one assistant turn go back in a single user message.
            if last and last["role"] == "user" and isinstance(last["content"], list) \
                    and all(b.get("type") == "tool_result" for b in last["content"]):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant":
            if m.get("_raw") is not None:
                out.append({"role": "assistant", "content": m["_raw"]})
                continue
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for call in m.get("tool_calls") or []:
                fn = call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": call["id"], "name": fn.get("name"), "input": args})
            out.append({"role": "assistant", "content": blocks or ""})
        else:
            out.append({"role": "user", "content": m.get("content") or ""})
    return "\n\n".join(s for s in system if s), out


def to_claude_tools(tools):
    return [{
        "name": t["function"]["name"],
        "description": t["function"].get("description", ""),
        "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}},
    } for t in tools]


class ClaudeLLM:
    def __init__(self, model, api_key=None, context_budget=400000, max_retries=4):
        self.anthropic = _sdk()
        self.model = model
        self.context_budget = context_budget
        kwargs = {"max_retries": max_retries}
        if api_key:
            kwargs["api_key"] = api_key  # otherwise the SDK resolves env vars or an `ant auth login` profile
        try:
            self.client = self.anthropic.Anthropic(**kwargs)
        except self.anthropic.AnthropicError as exc:
            raise LLMError(f"Could not set up Claude: {exc}. Run `tm setup` to add an API key.") from exc

    def list_models(self) -> list:
        return sorted(m.id for m in self._call(self.client.models.list))

    def chat(self, messages, tools=None, json_mode=False, temperature=None, max_tokens=None,
             tool_choice="auto") -> dict:
        # temperature is ignored: current Claude models reject sampling parameters.
        system, converted = to_claude(messages)
        if json_mode:
            system = (system + "\n\n" if system else "") + "Reply with only a JSON object, no other text."
        params = {"model": self.model, "max_tokens": MAX_TOKENS, "messages": converted}
        if system:
            params["system"] = system
        if tools:
            params["tools"] = to_claude_tools(tools)
            if tool_choice == "none":
                params["tool_choice"] = {"type": "none"}
        if self.model in FALLBACK_MODELS:
            # If a safety classifier declines, the API retries on a suitable model itself.
            response = self._call(self.client.beta.messages.create, betas=[FALLBACK_BETA],
                                  fallbacks="default", **params)
        else:
            response = self._call(self.client.messages.create, **params)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"Claude declined this request{f' ({category})' if category else ''}. "
                           "Rephrase the task or use another model.")
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [{"id": b.id, "type": "function",
                  "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                 for b in response.content if b.type == "tool_use"]
        return {"role": "assistant", "content": text, "tool_calls": calls or None, "_raw": response.content}

    def _call(self, fn, *args, **kwargs):
        a = self.anthropic
        try:
            return fn(*args, **kwargs)
        except a.AuthenticationError as exc:
            raise LLMError("Claude rejected the API key. Run `tm setup` to set a valid one.") from exc
        except a.PermissionDeniedError as exc:
            raise LLMError(f"This API key can't use {self.model}: {exc.message}") from exc
        except a.NotFoundError as exc:
            raise LLMError(f"Model '{self.model}' was not found. Run `tm models` to see the current ones.") from exc
        except a.RateLimitError as exc:
            raise LLMError("Claude rate limit reached (after retries). Wait a little and try again.") from exc
        except a.BadRequestError as exc:
            message = str(exc.message)
            if "too long" in message:
                raise RequestTooLarge(message) from exc
            if "tool" in message and "none" in message:
                raise ToolCallError(message) from exc
            raise LLMError(f"Claude rejected the request: {message}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("Could not reach the Claude API. Check your internet connection.") from exc
