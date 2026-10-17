"""Error log, bug reports and feedback, for testers and users to tell us what went wrong.

Nothing here is sent anywhere automatically. Reports are files the user can read
first; API keys and secret-looking values are always removed.
"""

import json
import platform
import shutil
import sys
import traceback
from datetime import datetime
from urllib.parse import urlencode

from taskmind import __version__, config
from taskmind.secrets import redact  # noqa: F401  (also used by callers)

# Set this to the project's "new issue" page once it's public, e.g.
# "https://github.com/<you>/taskmind/issues/new"; reports and feedback then open it prefilled.
ISSUES_URL = "https://github.com/Trivedi-Kartik/taskmind-cli/issues/new"
MAX_LOG_LINES = 200


def log_path():
    return config.home() / "errors.log"


def clean_argv(argv) -> list:
    """The command as typed, with keys removed (`tm config --key gsk_...`)."""
    out, hide_next = [], False
    for arg in argv:
        if hide_next:
            out.append("[REDACTED]")
            hide_next = False
        elif arg in ("--key",):
            out.append(arg)
            hide_next = True
        elif arg.startswith("--key="):
            out.append("--key=[REDACTED]")
        else:
            out.append(redact(arg))
    return out


def log_error(argv, message, exc=None):
    entry = {"at": datetime.now().isoformat(timespec="seconds"), "version": __version__,
             "command": " ".join(clean_argv(argv)), "message": redact(str(message))[:500]}
    if exc is not None:
        entry["traceback"] = redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))[-4000:]
    try:
        path = log_path()
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines = (lines + [json.dumps(entry, ensure_ascii=False)])[-MAX_LOG_LINES:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # logging must never cause a second failure


def recent_errors(n=10) -> list:
    path = log_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def system_info() -> dict:
    cfg = config.load()
    s = config.llm_settings(cfg)
    try:
        import rich  # noqa: F401  (only checking it's installed)
        has_rich = True
    except ImportError:
        has_rich = False
    return {
        "TaskMind": __version__,
        "Python": platform.python_version(),
        "System": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "AI provider": f"{s['label']} / {s['model'] or 'no model'} (key {'set' if s['api_key'] else 'missing'})",
        "Backups": ", ".join(cfg.get("fallback") or []) or "none",
        "Mode": cfg.get("mode") or "developer",
        "rich installed": "yes" if has_rich else "no",
        "git": "yes" if shutil.which("git") else "no",
        "Installed as": "single file (.pyz)" if ".pyz" in __file__ else (
            "package" if "site-packages" in __file__ else "source checkout"),
    }


def report_text(note="") -> str:
    info = system_info()
    lines = ["# TaskMind bug report", "", f"_Made {datetime.now():%Y-%m-%d %H:%M}. Contains no API keys "
             "and no code. Read it before sending._", ""]
    if note:
        lines += ["## What happened", "", note, ""]
    lines += ["## System", ""] + [f"- **{k}:** {v}" for k, v in info.items()]
    errors = recent_errors()
    lines += ["", "## Recent errors", ""]
    if not errors:
        lines.append("None recorded.")
    for e in errors:
        lines.append(f"- `{e.get('at')}` `tm {e.get('command')}`: {e.get('message')}")
        if e.get("traceback"):
            lines += ["", "```", e["traceback"].strip(), "```"]
    return "\n".join(lines) + "\n"


def issue_link(title, body) -> str:
    """A prefilled 'new issue' page, or "" when no issue tracker is configured."""
    if not ISSUES_URL:
        return ""
    return f"{ISSUES_URL}?{urlencode({'title': title, 'body': body[:5500]})}"


def is_debug() -> bool:
    import os
    return bool(os.environ.get("TASKMIND_DEBUG"))


def python_info() -> str:
    return f"{sys.executable} {platform.python_version()}"
