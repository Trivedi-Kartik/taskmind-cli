"""Knowing about and installing new versions: a once-a-day check and `tm update`.

The check asks GitHub for one small public file (the version number) at most once a
day, with a short time limit, and only when a person is at the terminal. Nothing about
the user or their work is sent. `tm config --update-check off` turns it off.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

from taskmind import __version__, config

REPO = "https://github.com/Trivedi-Kartik/taskmind-cli"
SOURCE = f"git+{REPO}.git"
VERSION_URL = "https://raw.githubusercontent.com/Trivedi-Kartik/taskmind-cli/main/taskmind/__init__.py"
CHANGELOG_URL = "https://raw.githubusercontent.com/Trivedi-Kartik/taskmind-cli/main/CHANGELOG.md"


def parse_version(text) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def newer(latest, current=__version__) -> bool:
    return bool(latest) and parse_version(latest) > parse_version(current)


def _fetch(url, timeout) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"taskmind/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def latest_version(timeout=3):
    """The newest version on GitHub, or None if it can't be reached."""
    try:
        match = re.search(r'__version__\s*=\s*"([^"]+)"', _fetch(VERSION_URL, timeout))
    except (OSError, ValueError):
        return None
    return match.group(1) if match else None


def _state_path() -> Path:
    return config.home() / "update-check.json"


def check_daily():
    """The newer version to mention, if any. Asks GitHub at most once a day."""
    if os.environ.get("CI") or config.load().get("update_check") == "off":
        return None
    path = _state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    today = date.today().isoformat()
    if state.get("checked") != today:
        state = {"checked": today, "latest": latest_version(timeout=2) or state.get("latest")}
        try:
            path.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass
    return state["latest"] if newer(state.get("latest")) else None


def install_kind() -> str:
    """How this copy of TaskMind was installed: pipx, pip, source or pyz."""
    here = Path(__file__).resolve()
    if ".pyz" in str(here):
        return "pyz"
    if (here.parent.parent / ".git").exists():
        return "source"
    if "pipx" in sys.prefix.replace("\\", "/").split("/"):
        return "pipx"
    return "pip"


def update_command(kind):
    """The command that updates this kind of install, or None when it must be done by hand."""
    if kind == "pipx":
        return ["pipx", "install", "--force", SOURCE] if shutil.which("pipx") else None
    if kind == "pip":
        return [sys.executable, "-m", "pip", "install", "--upgrade", "--user", SOURCE]
    if kind == "source":
        return ["git", "-C", str(Path(__file__).resolve().parent.parent), "pull", "--ff-only"]
    return None


def whats_new(since=__version__, limit=3) -> str:
    """The CHANGELOG entries newer than `since` (at most `limit`), or ""."""
    try:
        text = _fetch(CHANGELOG_URL, timeout=5)
    except (OSError, ValueError):
        return ""
    entries = re.split(r"(?m)^(?=## )", text)
    keep = [e.strip() for e in entries if e.startswith("## ") and newer(e[3:].split()[0], since)]
    return "\n\n".join(keep[:limit])
