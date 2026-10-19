"""What TaskMind knows about a project before it starts work on it.

Two sources, both put in front of the AI at the start of every build:
  - Project notes: files developers already keep for AI tools (CLAUDE.md, AGENTS.md,
    .cursorrules, Copilot instructions) and TaskMind's own TASKMIND.md (`tm init` writes one).
    A team already using Claude Code or Cursor gets TaskMind working with no setup.
  - Mentions: `@src/app.py` or `@src/forms/` in a request attaches that file, or a folder's
    file list, so the AI starts from exactly what the person pointed at.

Secret files are never read, and secret-looking values are blanked, as everywhere else.
"""

import re
from pathlib import Path

from taskmind.repo_tools import Repo
from taskmind.secrets import is_secret_file, redact

NOTE_FILES = ["TASKMIND.md", "CLAUDE.md", "AGENTS.md", ".cursorrules", ".github/copilot-instructions.md"]

# "@src/app.py" but not "name@example.com": the @ must start a word.
_MENTION = re.compile(r"(?<![\w@])@([\w.\-/]+[\w/])")


def project_notes(root, limit=12_000) -> list:
    """[(file name, text)] for the project's instruction files, within `limit` characters in all."""
    root = Path(root)
    notes, used = [], 0
    for name in NOTE_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = redact(path.read_text(encoding="utf-8", errors="replace")).strip()
        except OSError:
            continue
        if not text or used >= limit:
            continue
        if used + len(text) > limit:
            text = text[: limit - used] + "\n... (cut to fit)"
        notes.append((name, text))
        used += len(text)
    return notes


def mentions(text) -> list:
    """The paths a request points at with @, in order, without repeats."""
    return list(dict.fromkeys(m.rstrip(".") for m in _MENTION.findall(text or "")))


def missing_mentions(root, text) -> list:
    """Mentioned paths that don't exist in the project (typos are worth catching before a build)."""
    root = Path(root)
    return [m for m in mentions(text) if not (root / m).exists()]


def attachments(repo: Repo, text, limit=24_000) -> tuple:
    """(text to give the AI, [paths attached]) for the request's @mentions, within `limit` characters."""
    parts, attached, used = [], [], 0
    for rel in mentions(text):
        path = repo.root / rel
        if not path.exists():
            continue
        try:
            repo._safe(rel)
        except ValueError:
            continue  # outside the project
        if path.is_dir():
            body = repo.tree(rel, max_entries=200)
            title = f"Folder {rel}/ (its files; read the ones you need)"
        elif is_secret_file(rel):
            parts.append(f"### {rel}\n(a secrets file: TaskMind never reads it)")
            continue
        else:
            try:
                body = redact(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable
            title = f"File {rel}"
        room = limit - used
        if room <= 200:
            parts.append(f"### {rel}\n(not attached: no room left; read it with read_file)")
            continue
        if len(body) > room:
            body = body[:room] + "\n... (cut to fit; read the rest with read_file)"
        parts.append(f"### {title}\n{body}")
        attached.append(rel)
        used += len(body)
    return "\n\n".join(parts), attached


def code_context(repo: Repo, text, limit=4000, focus=(), event=None, excerpt_chars=0) -> tuple:
    """(text for the AI, info) from the code index: a short map of the project and the files the
    request most likely concerns. ("", {}) for small projects, which the AI can simply list."""
    from taskmind.codeindex import MAP_MIN_FILES
    if not repo.index_wanted(MAP_MIN_FILES):
        return "", {}
    from taskmind.codeindex import Index
    if event and not Index.saved_exists(repo):
        event("indexing", files=repo.code_file_count())  # the first run reads every file once
    index = repo.index
    guess = index.relevant(text, limit=8, exclude=set(focus))
    parts = ["Project map, computed by TaskMind from the code (use it to go straight to the right files):\n"
             + index.map_text(limit=limit, focus=focus)]
    if guess:
        parts.append("Files this request probably concerns, with the definitions in them whose names match "
                     "(start with these; they may be wrong):\n" + "\n".join(
                         f"  {path}" + (": " + ", ".join(f"{name} (line {line})" for name, line in names)
                                       if names else "") for path, names in guess))
    shown = []
    room = excerpt_chars
    if room >= 1200:  # roomy providers: show the code itself, not just where it is
        pieces = []
        for path, qualified, line, end in index.best_definitions(text, limit=4, exclude=set(focus)):
            code = index.excerpt(path, line, end, max_lines=45 if room >= 4000 else 25)
            if code and len(code) <= room:
                pieces.append(f"--- {path}: {qualified} ---\n{code}")
                shown.append(f"{path}:{qualified}")
                room -= len(code)
            if len(pieces) >= 3:
                break
        if pieces:
            parts.append("The code of the best-matching definitions, so you can start from it (they may not "
                         "be the right ones):\n\n" + "\n\n".join(pieces))
    info = {**index.summary(), "relevant": [path for path, _ in guess], "shown": shown}
    return "\n\n".join(parts), info


INIT_GOAL = """Study this project so that an AI engineer can work on it well later, then write its \
project notes: TASKMIND.md, read at the start of every TaskMind build.

Explore with your tools: the folder structure, README, package/build files (package.json, \
pyproject.toml, requirements.txt, go.mod, Cargo.toml ...), the main entry points, and a test.

Write short, factual Markdown (under 60 lines) with these sections:
# <project name>
One or two sentences: what it is and who it's for.
## Stack
Languages, frameworks, main libraries, with versions when the files say.
## Layout
The important folders and files, one line each.
## Run and test
Exact commands to install, run and test it (only ones you found evidence for).
## Conventions
Style and patterns the code follows (naming, structure, error handling, how tests are written).
## Watch out
Anything easy to break or get wrong.

Only state what you saw in the files; write "unknown" rather than guess. No secrets.
Final answer: only this JSON: {"notes": "<the Markdown>"}"""

INIT_HINT = ("Use your tools first: list the files, then read the README, the package files and one or two "
             "core files. Be economical with tool calls.")


def write_notes(llm, repo: Repo, known_check="", on_step=None) -> str:
    """Ask the AI to study the project and return TASKMIND.md's text."""
    from taskmind.agent import run_tool_agent
    from taskmind.llm import extract_object

    goal = INIT_GOAL
    if known_check:
        goal += f"\n\nTaskMind found this test command: {known_check}"
    overview, _ = code_context(repo, "", limit=min(6000, getattr(llm, "context_budget", 9000) // 6))
    if overview:
        goal += "\n\n" + overview

    def parses(text):
        try:
            return bool(str(extract_object(text).get("notes") or "").strip())
        except ValueError:
            return False

    text = run_tool_agent(llm, "You are a senior engineer documenting a codebase for a colleague.", goal, repo,
                          INIT_HINT, max_steps=12, on_step=on_step, accept=parses)
    notes = str(extract_object(text).get("notes") or "").strip()
    if not notes:
        raise ValueError("The AI didn't write any notes. Try again.")
    return redact(notes) + "\n"


ASK_SYSTEM = """You are TaskMind, a senior engineer answering a question about the user's project. \
Read the code with your tools before answering; don't guess. Answer directly and in plain words, name the files \
and lines you relied on (path:line), and say so when the code doesn't tell you. You only look: never suggest that \
you have changed anything."""


def answer_code_question(llm, repo: Repo, question, memory=(), on_step=None) -> str:
    """Answer a question about the project from its code (read-only), with the same head start a build gets:
    project notes, files named with @, and the index's map and best-matching code."""
    from taskmind.agent import run_tool_agent
    budget = getattr(llm, "context_budget", 9000)
    parts = []
    for name, text in project_notes(repo.root, limit=min(6000, budget // 6)):
        parts.append(f"--- {name} (the project's own notes) ---\n{text}")
    attached, _ = attachments(repo, question, limit=budget // 4)
    if attached:
        parts.append("Files and folders the user pointed at:\n\n" + attached)
    overview, _ = code_context(repo, question, limit=min(6000, budget // 8), excerpt_chars=min(6000, budget // 12))
    if overview:
        parts.append(overview)
    if memory:
        parts.append("The conversation so far:\n" + "\n".join(f"Q: {q}\nA: {a[:500]}" for q, a in list(memory)[-3:]))
    goal = "\n\n".join(parts + [f"Question: {question}"])
    hint = "Use your tools to read the relevant code first. Be economical: you have a limited number of calls."
    return run_tool_agent(llm, ASK_SYSTEM, goal, repo, hint, max_steps=14, on_step=on_step,
                          final_json=False).strip()
