"""Which AI handles each stage: per-stage choices, automatic failover, independent review.

Every stage saves its result before the next one starts, so when a provider fails
(daily limit, outage, bad key) the current stage simply reruns on the next
provider and nothing already done is lost.
"""

from taskmind import config
from taskmind.llm import LLMError, from_settings

STAGES = ["clarify", "analyze", "plan", "build", "verify", "sync", "today", "ask", "do"]
STAGE_HELP = {
    "clarify": "questions and task definition",
    "analyze": "code analysis and security check",
    "plan": "subtasks and test cases",
    "build": "writing, running and fixing the code (`tm build`)",
    "verify": "checking written code against the plan",
    "sync": "matching commits to subtasks (`tm sync --ai`)",
    "today": "morning briefing (`tm today --ai`)",
    "ask": "answering questions (`tm ask`)",
    "do": "understanding requests in your own words (`tm do`)",
}


def parse_target(text):
    """'groq' -> ('groq', None); 'groq:openai/gpt-oss-20b' -> ('groq', 'openai/gpt-oss-20b')."""
    provider, _, model = text.strip().partition(":")
    if provider not in config.PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Choose one of: {', '.join(config.PROVIDERS)}")
    return provider, model or None


def describe(s) -> str:
    return f"{s['label']} / {s['model']}"


def _usable(s) -> bool:
    return bool(s["model"]) and not (s["needs_key"] and not s["api_key"])


def _settings(cfg, provider, model=None):
    s = config.llm_settings(cfg, provider)
    return {**s, "model": model or s["model"]}


def candidates(cfg, stage) -> list:
    """Providers to try for a stage, in order: stage override, main provider, backups."""
    order = []
    override = cfg.get("stages", {}).get(stage)
    if override:
        order.append(_settings(cfg, *parse_target(override)))
    order.append(_settings(cfg, cfg["provider"]))
    for entry in cfg.get("fallback", []):
        # "gemini" or "groq:openai/gpt-oss-20b": another model on the same provider has its own limits.
        try:
            order.append(_settings(cfg, *parse_target(entry)))
        except ValueError:
            continue
    seen, result = set(), []
    for s in order:
        key = (s["provider"], s["model"])
        if key not in seen and _usable(s):
            seen.add(key)
            result.append(s)
    return result


def review_candidates(cfg, planned_by=None) -> tuple:
    """Candidates for `tm verify`, preferring an AI other than the one that wrote or planned the code.

    Returns (candidates, note). An explicit verify stage override or review target wins.
    """
    cands = candidates(cfg, "verify")
    review = cfg.get("review", "auto")
    if review == "off" or cfg.get("stages", {}).get("verify"):
        return cands, None
    if review != "auto":
        chosen = _settings(cfg, *parse_target(review))
        if _usable(chosen):
            rest = [c for c in cands if (c["provider"], c["model"]) != (chosen["provider"], chosen["model"])]
            return [chosen] + rest, None
    avoid = {p for p in (cfg.get("coder"), planned_by) if p}
    if not avoid:
        return cands, None
    independent = [c for c in cands if c["provider"] not in avoid]
    others = [c for c in cands if c["provider"] in avoid]
    if independent:
        return independent + others, None
    names = " and ".join(config.PROVIDERS[p]["label"] for p in sorted(avoid))
    return cands, (f"Not an independent review: no AI other than {names} is set up. Add another "
                   "provider with `tm setup` to get a second opinion.")


def run(cands, fn, on_switch=None):
    """Call fn(llm) on each candidate until one succeeds. Returns (result, settings used)."""
    if not cands:
        raise LLMError("No AI provider is ready. Run `tm setup`.")
    errors = []
    for i, s in enumerate(cands):
        try:
            return fn(from_settings(s)), s
        except LLMError as exc:
            errors.append(f"{describe(s)}: {exc}")
            if i + 1 < len(cands):
                if on_switch:
                    on_switch(s, cands[i + 1], exc)
                continue
            if len(errors) == 1:
                raise
            raise LLMError("Every configured provider failed:\n  " + "\n  ".join(errors)) from exc
