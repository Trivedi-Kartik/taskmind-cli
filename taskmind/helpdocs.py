"""Built-in help: the README as searchable topics, used by `tm help` and `tm ask`.

Everything here works offline, so help is available even when no AI provider
works (the moment people most need it, e.g. a rejected API key).
"""

import difflib
import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"  # in a source checkout


def guide_text() -> str:
    """The user guide: bundled in the package (pip install, .pyz), or the checkout's README."""
    try:
        from importlib.resources import files
        bundled = files("taskmind").joinpath("guide.md")
        if bundled.is_file():
            return bundled.read_text(encoding="utf-8")
    except (ImportError, OSError, TypeError, ValueError):
        pass
    return README.read_text(encoding="utf-8") if README.exists() else ""
STOPWORDS = set("""a an and are as at be but by can do does for from get got have how i if in into is it its
me my of on or so that the this to use using was what when where which who why will with you your
tm taskmind please want need know""".split())
# Everyday words people use for things the guide names differently.
SYNONYMS = {"key": "api", "keys": "api", "token": "api", "password": "api", "broken": "troubleshooting",
            "error": "troubleshooting", "fail": "troubleshooting", "failing": "troubleshooting",
            "working": "troubleshooting", "subtask": "step", "subtasks": "step", "steps": "step",
            "model": "models", "provider": "providers", "chatgpt": "openai", "claude": "anthropic",
            "gemini": "gemini", "grok": "xai", "alarm": "reminders", "notify": "reminders",
            "notification": "reminders", "speak": "voice", "talk": "voice", "mic": "voice",
            "microphone": "voice", "commit": "track", "git": "track", "check": "verify",
            "review": "verify", "begin": "tutorial", "beginner": "tutorial", "first": "tutorial",
            "new": "tutorial", "start": "tutorial", "started": "tutorial", "confused": "tutorial",
            "understand": "tutorial", "overview": "tutorial"}


def sections() -> list:
    """[(title, body)] for every ## and ### heading in the guide."""
    text = guide_text()
    if not text:
        return []
    found, title, body = [], None, []
    for line in text.splitlines():
        m = re.match(r"^#{2,3} (.+)$", line)
        if m:
            if title and title != "Contents":
                found.append((title, "\n".join(body).strip()))
            title, body = m.group(1).strip(), []
        elif title:
            body.append(line)
    if title and title != "Contents":
        found.append((title, "\n".join(body).strip()))
    return found


def _words(text) -> list:
    words = re.findall(r"[\w-]+", text.lower())
    out = []
    for w in words:
        if w in STOPWORDS or len(w) < 2:
            continue
        out.append(w)
        if w in SYNONYMS:
            out.append(SYNONYMS[w])
        if w.endswith("s") and len(w) > 3:
            out.append(w[:-1])
    return out


def search(question, k=3) -> list:
    """The k sections that best match a question, best first."""
    terms = _words(question)
    if not terms:
        return []
    scored = []
    wanted = {w.rstrip("s") for w in terms}
    for title, body in sections():
        t, b = title.lower(), body.lower()
        score = sum(5 * t.count(w) + min(b.count(w), 6) for w in terms)
        title_terms = {w.rstrip("s") for w in _words(title)}
        if title_terms and title_terms <= wanted:  # the whole section is about what was asked
            score += 15
        if score:
            scored.append((score, title, body))
    scored.sort(key=lambda x: -x[0])
    return [(title, body) for _, title, body in scored[:k]]


def slug(title) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def find_topic(name):
    """Best section for a topic name like 'reminders' or 'api keys', or None."""
    all_sections = sections()
    wanted = slug(name)
    exact = [(t, b) for t, b in all_sections if slug(t) == wanted]
    if exact:
        return exact[0]
    prefixed = sorted(((t, b) for t, b in all_sections if slug(t).startswith(wanted)), key=lambda x: len(x[0]))
    if prefixed:  # "remind" -> "Reminders", not "Reminders and calendar"
        return prefixed[0]
    word = wanted.rstrip("s")
    titled = sorted(((t, b) for t, b in all_sections if word in {w.rstrip("s") for w in _words(t)}),
                    key=lambda x: len(x[0]))
    if titled:  # "step" -> "Your own steps"
        return titled[0]
    close = difflib.get_close_matches(wanted, [slug(t) for t, _ in all_sections], n=1, cutoff=0.6)
    if close:
        return next((t, b) for t, b in all_sections if slug(t) == close[0])
    results = search(name, k=1)
    return results[0] if results else None


def excerpt(body, lines=25) -> str:
    kept = body.splitlines()
    return "\n".join(kept[:lines]) + ("\n..." if len(kept) > lines else "")
