"""Settings stored in ~/.taskmind/config.json (override the folder with TASKMIND_HOME)."""

import json
import os
from pathlib import Path

# "kind" picks the client: "openai" covers every OpenAI-compatible API, "anthropic"
# uses the official Anthropic SDK. "model" is a suggestion that `tm setup` only
# offers when the provider's live model list still contains it (models get
# retired). "budget" is how many characters of conversation the agent keeps:
# Groq's free tier allows only 8k tokens per minute, the others are far roomier.
PROVIDERS = {
    "groq": {
        "label": "Groq", "cost": "free tier", "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key_env": "GROQ_API_KEY",
        "signup": "https://console.groq.com/keys",
        "budget": 9000,
    },
    "gemini": {
        "label": "Google Gemini", "cost": "free tier", "kind": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "key_env": "GEMINI_API_KEY",
        "signup": "https://aistudio.google.com/apikey",
        "budget": 200000,  # ~50k tokens a request: the free tier allows ~250k a minute
    },
    "openrouter": {
        "label": "OpenRouter", "cost": "free models available", "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "model": None,
        "key_env": "OPENROUTER_API_KEY",
        "signup": "https://openrouter.ai/keys",
        "budget": 30000,
    },
    "ollama": {
        "label": "Ollama (local)", "cost": "free, runs on your machine", "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "model": None,
        "key_env": None,
        "signup": "https://ollama.com/download (no key needed)",
        "budget": 30000,
    },
    "openai": {
        "label": "OpenAI (ChatGPT)", "cost": "paid", "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": None,
        "key_env": "OPENAI_API_KEY",
        "signup": "https://platform.openai.com/api-keys",
        "budget": 60000,
        # Current OpenAI models take max_completion_tokens and reject custom temperatures.
        "max_tokens_field": "max_completion_tokens",
        "temperature": False,
    },
    "anthropic": {
        "label": "Anthropic (Claude)", "cost": "paid, needs `pip install anthropic`", "kind": "anthropic",
        "base_url": None,
        "model": "claude-opus-5-5",
        "key_env": "ANTHROPIC_API_KEY",
        "key_optional": True,  # the SDK also finds `ant auth login` profiles
        "signup": "https://platform.claude.com/settings/keys",
        "budget": 400000,
    },
    "xai": {
        "label": "xAI (Grok)", "cost": "paid", "kind": "openai",
        "base_url": "https://api.x.ai/v1",
        "model": None,
        "key_env": "XAI_API_KEY",
        "signup": "https://console.x.ai",
        "budget": 60000,
    },
}

DEFAULTS = {
    "provider": "groq", "keys": {}, "models": {}, "base_url": None,
    "fallback": [],    # backup providers, tried in order when one fails or hits its limit
    "stages": {},      # optional per-stage choice, e.g. {"analyze": "anthropic:claude-opus-5-5"}
    "review": "auto",  # who runs `tm verify`: auto (an AI other than coder/planner), off, or provider[:model]
    "coder": None,     # the provider whose coding tool you use (e.g. anthropic for Claude Code)
}


def home() -> Path:
    path = Path(os.environ.get("TASKMIND_HOME") or Path.home() / ".taskmind")
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return home() / "config.json"


def db_path() -> Path:
    return home() / "taskmind.db"


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    path = config_path()
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    # Older configs kept one key and model for the active provider; keep them per provider.
    provider = cfg.get("provider") or "groq"
    if cfg.get("api_key"):
        cfg["keys"].setdefault(provider, cfg["api_key"])
    if cfg.get("model"):
        cfg["models"].setdefault(provider, cfg["model"])
    cfg.pop("api_key", None)
    cfg.pop("model", None)
    return cfg


def save(cfg: dict) -> None:
    path = config_path()
    # Create with owner-only permissions so API keys are never readable by others, even briefly.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(cfg, indent=2))
    path.chmod(0o600)  # also fixes files created by older versions


def llm_settings(cfg: dict, provider=None) -> dict:
    """Resolve provider defaults, config values and env vars (env wins for the key)."""
    provider = provider or cfg.get("provider") or "groq"
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")
    spec = PROVIDERS[provider]
    key_env = spec["key_env"]
    api_key = (os.environ.get(key_env) if key_env else None) or cfg.get("keys", {}).get(provider)
    return {
        **spec,
        "provider": provider,
        "base_url": (cfg.get("base_url") if provider == cfg.get("provider") else None) or spec["base_url"],
        "model": cfg.get("models", {}).get(provider) or spec["model"],
        "api_key": api_key,
        "needs_key": bool(key_env) and not spec.get("key_optional"),
    }
