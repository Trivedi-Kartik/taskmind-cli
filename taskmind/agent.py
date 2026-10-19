"""Tool-using agent loop, tuned for small free-tier token budgets."""

import json

from taskmind.llm import RequestTooLarge, ToolCallError, chat_without_tools
from taskmind.repo_tools import Repo, dispatch, tool_specs

TRIMMED = "[older tool output removed to save tokens]"
REPEATED = "You already made this exact call. Use what you learned, or try something else."


def _size(messages) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def _compact(messages, budget):
    """Drop the oldest tool outputs until the conversation fits the budget."""
    for msg in messages:
        if _size(messages) <= budget:
            return
        if msg["role"] == "tool" and msg["content"] != TRIMMED:
            msg["content"] = TRIMMED


def _chat_within_budget(llm, messages, budget, tools, **kw):
    """Call the model, shrinking the conversation if the provider says it is too large,
    and retrying once if the model garbles a tool call."""
    retried = False
    while True:
        _compact(messages, budget)
        try:
            return llm.chat(messages, tools=tools, **kw)
        except RequestTooLarge:
            if budget <= 4000:
                raise
            budget //= 2
        except ToolCallError:
            if retried:
                raise
            retried = True


def _findings(messages, budget) -> str:
    """Tool results, newest first until the budget is used, returned in call order."""
    calls = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            calls[call.get("id")] = f"{fn.get('name')}({fn.get('arguments')})"
    notes, used = [], 0
    for msg in reversed(messages):
        if msg["role"] != "tool" or msg["content"] == TRIMMED or msg["content"].startswith(REPEATED):
            continue
        note = f"### {calls.get(msg['tool_call_id'], 'tool')}\n{msg['content']}"
        if used + len(note) > budget:
            break
        notes.append(note)
        used += len(note)
    return "\n\n".join(reversed(notes))


def run_tool_agent(llm, system, user, repo: Repo, explore_hint="", max_steps=10, budget=None,
                   on_step=None, accept=None) -> str:
    """Let the model explore the repo with tools, then return its final text answer.

    Free tiers like Groq's cap tokens per minute (8k), and they count the *reserved*
    reply size (max_tokens) too. So the conversation is kept to ~2.5k tokens and
    exploration steps reserve only a small reply; tool-call replies are tiny anyway.

    If the model is still exploring when the steps run out (or its tool calling
    breaks after some progress), a separate tool-free call writes the answer from
    what was found: open models often keep calling tools even when told to stop.

    `user` is the goal and answer format; `explore_hint` (how to use the tools) is
    only shown while tools are available, so the write-up step is not told to use them.
    `accept` checks a direct answer (e.g. that it parses); a rejected answer goes to
    the write-up step, which keeps the findings instead of starting over.
    """
    # Providers say how much conversation they can take; Groq's free tier is the tightest.
    budget = budget or getattr(llm, "context_budget", 9000)
    first = f"{user}\n\n{explore_hint}" if explore_hint else user
    messages = [{"role": "system", "content": system}, {"role": "user", "content": first}]
    seen = set()
    tools = tool_specs(repo)
    for step in range(max_steps):
        try:
            reply = _chat_within_budget(llm, messages, budget, tools, max_tokens=1000)
        except ToolCallError:
            if not seen:
                raise  # nothing explored yet; the caller has its own fallback
            break
        calls = reply.get("tool_calls") or []
        if not calls:
            content = reply.get("content") or ""
            if accept is None or accept(content):
                return content
            break
        assistant = {"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls}
        if reply.get("_raw") is not None:
            assistant["_raw"] = reply["_raw"]  # Claude needs its reply blocks back unchanged
        messages.append(assistant)
        left = max_steps - step - 1
        note = "Wrap up: give your final answer next." if left <= 1 else f"{left} tool calls left."
        for call in calls:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):  # e.g. '["src"]' from a confused model
                args = {}
            key = (fn.get("name"), json.dumps(args, sort_keys=True))
            if key in seen:
                result = REPEATED
            else:
                seen.add(key)
                if on_step:
                    on_step(fn.get("name"), args)
                result = f"{dispatch(repo, fn.get('name', ''), args)}\n[{note}]"
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})

    final = chat_without_tools(
        llm,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"{user}\n\nThe repository has already been explored. What was found:\n\n"
                f"{_findings(messages, budget)}\n\n"
                "Exploration is over and no tools are available. Using only the findings above, "
                "give your final answer now."
            )},
        ],
        json_mode=True,
        max_tokens=3000,
    )
    return final.get("content") or ""
