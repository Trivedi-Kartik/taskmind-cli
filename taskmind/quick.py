"""Quick mode: TaskMind finds the code itself, and the AI answers with the edit in a few requests.

The step-by-step way (taskmind/builder.py) makes one AI request per step, 25 to 60 for even a small change,
and every request re-sends the whole conversation. On a free tier that is one task per day at best.

Quick mode does the looking itself, with no AI request: project notes, files pointed at with @, a map of the
project, the files and functions the request probably concerns (from the code index), or the whole project when
it is small. The AI then answers once with the edits (as JSON); TaskMind applies them, runs the project's
checks, and sends back only what failed. Measured on one real task: 5 requests and 8 seconds, against
25 to 44 steps and 4 to 10 minutes. When a task doesn't suit it (too big, the AI can't say what to change, the
edits won't apply, the checks keep failing) it hands over to the step-by-step mode, with what it learned.
"""

import difflib
import os
import re

from taskmind import builder, project
from taskmind.builder import Meter, _audience, _edit_variants, detect_check
from taskmind.llm import extract_object
from taskmind.secrets import is_secret_file, redact

MIN_BUDGET = 30_000      # characters of conversation the provider can take; tiny free tiers (Groq) can't
MAX_REQUESTS = 10        # then it hands over: a task that needs more is a step-by-step task
MAX_NEED_ROUNDS = 4
QUICK_FIX_ROUNDS = 2
WHOLE_PROJECT_CHARS = 40_000
MAX_EDITS, MAX_FILES = 40, 12

SYSTEM = """You are TaskMind's engineer, making a change in the user's project. You are given the task and the \
code that matters (the whole project when it is small, otherwise a map of it plus the files and functions the task \
probably concerns).

Reply with ONLY a JSON object:
{"summary": "one or two sentences: what you changed and how to use it",
 "edits": [{"path": "...", "old": "...", "new": "..."}],
 "files": [{"path": "...", "content": "..."}],
 "check_command": "a command that proves it works and exits 0 on success (only if the project has no tests)",
 "need": ["..."],
 "too_big": false}

Rules:
- Only edit code you have actually been shown in this conversation. If the function or file you need to change \
has not been shown, ask for it with `need` first: never write `old` from memory or guess what a function contains.
- Make the whole change in this one reply when you can: `edits` change existing files, `files` create new ones \
(or replace a small file completely), and add or update tests with the project's test setup.
- `old` must be copied EXACTLY from the code shown (without line numbers) and appear exactly once in the file: \
include two or three neighbouring lines to make it unique. Keep each edit small.
- If you can't see code you need, set `need` instead and leave edits empty. You can ask for: a function or \
class name ("Index.summary" or "src/app.py: Index.summary"), a file path (an outline of it, or the whole file when it is small), a line range \
("src/app.py:120-180"), a folder, or a text search ("search:footer-bottom"). Ask for everything you need in one go (a list). You get at most four such rounds.
- If the task needs exploring many files, or can't be done safely with a few edits, set "too_big": true and \
write nothing else.
- Follow the project's own style and notes. Never touch secret files (.env, keys). Never weaken or delete \
existing tests to make them pass. Don't run anything: TaskMind runs the project's checks itself."""


# --- what the AI is shown ---------------------------------------------------------------------

def _whole_project(ws, limit) -> str:
    """Every text file of a small project, in full, or "" when it doesn't fit."""
    parts, used = [], 0
    for name in ws.repo.files():
        if name in project.NOTE_FILES:
            continue  # shown separately, as the project's notes
        try:
            path = ws.repo._safe(name)
            if path.stat().st_size > 30_000:
                return ""
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        used += len(text)
        if used > limit:
            return ""
        parts.append(f"--- {name} ---\n{redact(text)}")
    return "\n\n".join(parts)


def gather(ws, task, budget, event, language, simple, earlier, known_check) -> str:
    """The first message: everything TaskMind can work out without asking the AI."""
    goal = f"Earlier work in this project (it's in the code now):\n{earlier}\n\n" if earlier else ""
    goal += f"Task: {task}\n\n{_audience(language, simple)}"
    if known_check:
        goal += f"\n\nThe project's tests run with: {known_check}. They must pass."
    notes = project.project_notes(ws.root, limit=min(12_000, budget // 4))
    if notes:
        goal += "\n\nProject notes (follow them):\n\n" + "\n\n".join(f"--- {n} ---\n{t}" for n, t in notes)
    attached_text, attached = project.attachments(ws.repo, task, limit=budget // 4)
    if attached_text:
        goal += "\n\nFiles and folders the user pointed at:\n\n" + attached_text
    whole = _whole_project(ws, min(WHOLE_PROJECT_CHARS, budget // 5))
    info = {}
    if whole:
        goal += "\n\nThe whole project (it's small):\n\n" + whole
    else:
        text, info = project.code_context(
            ws.repo, task, limit=min(8000, budget // 6), event=event, min_files=1,
            focus=[a for a in attached if not a.endswith("/")],
            excerpt_chars=min(16_000, budget // 8), excerpt_lines=70, excerpt_count=4)
        goal += "\n\n" + text
    event("context", notes=[n for n, _ in notes], attached=attached, index=info)
    return goal


ASK_FORMS = ('You can ask for: a function or class name ("Index.summary", or "src/app.py: Index.summary"), a file path '
             '(an outline of it, or the whole file when it is small), a line range ("src/app.py:120-180"), a folder, or '
             'a text search ("search:footer-bottom").')


def _symbol_pieces(ws, symbol, path=None) -> str:
    """The code of a definition from the index, preferring the file the AI named; else a text search for it."""
    repo = ws.repo
    bare = symbol.rsplit(".", 1)[-1].lower()
    hits = [h for h in repo.index.definitions(bare)
            if symbol.lower() in (h[3].lower(), h[3].rsplit(".", 1)[-1].lower())]
    if path:
        hits = [h for h in hits if h[0] == path] or hits
    if hits:
        return "\n\n".join(f"--- {h[0]}: {h[3]} ---\n" + repo.index.excerpt(h[0], h[1], h[4], max_lines=90)
                           for h in hits[:2])
    found = repo.search(re.escape(symbol), max_results=12)  # not a definition the index knows: look for the text
    if path and path in repo.files():
        found = "\n".join(line for line in found.splitlines() if line.startswith(path + ":")) or found
    return "" if found.startswith("No matches") else f"--- text search for {symbol} ---\n{found}"


def _resolve_one(ws, want) -> str:
    """The code for one thing the AI asked for ("" when there is nothing)."""
    repo = ws.repo
    want = want.strip().strip("`'\"")
    if want.lower().startswith("search:"):
        found = repo.search(want[7:].strip(), max_results=20)
        return "" if found.startswith(("No matches", "Invalid")) else f"--- search {want[7:].strip()} ---\n{found}"
    rng = re.match(r"^(.+?):(\d+)(?:-(\d+))?$", want)
    if rng and _clean_path(rng.group(1)) in repo.files():
        path, start = _clean_path(rng.group(1)), int(rng.group(2))
        if rng.group(3):
            end = int(rng.group(3))
        else:  # one line: that line and the ones around it
            start, end = max(1, start - 30), start + 30
        end = min(end, start + 199)
        return f"--- {path}:{start}-{end} ---\n" + _numbered(repo, path, start, end)
    path_only = _clean_path(want)
    if path_only in repo.files():
        from taskmind.codeindex import language_of
        if language_of(path_only) and _line_count(repo, path_only) > 150:
            return (f"--- outline of {path_only} (ask for a function by name, or a range like {path_only}:10-60) ---\n"
                    + repo.index.outline(path_only))
        return f"--- {path_only} ---\n" + _numbered(repo, path_only, 1, 400)
    if path_only.endswith("/") or any(f.startswith(path_only.rstrip("/") + "/") for f in repo.files()):
        return f"--- files in {path_only} ---\n" + repo.tree(path_only.rstrip("/") or ".", max_entries=80)
    # "path: Symbol", "path::Symbol", "path Symbol", "Symbol in path"
    named = (re.match(r"^(?P<path>[\w./\\-]+\.\w+)\s*(?:::|:|\s)\s*(?P<sym>[A-Za-z_][\w.]*)(?:\(\))?$", want)
             or re.match(r"^(?P<sym>[A-Za-z_][\w.]*)(?:\(\))?\s+(?:in|from|of)\s+(?P<path>[\w./\\-]+\.\w+)$", want))
    if named:
        return _symbol_pieces(ws, named.group("sym"), _clean_path(named.group("path")))
    return _symbol_pieces(ws, want.removesuffix("()"))


def resolve_needs(ws, wants, limit=14_000) -> tuple:
    """(code, found anything) for what the AI asked to see."""
    pieces, used, found = [], 0, False
    for want in [str(w).strip() for w in wants][:8]:
        if not want:
            continue
        piece = _resolve_one(ws, want)
        found = found or bool(piece)
        piece = piece or f"--- {want} ---\nNothing found for that."
        if used + len(piece) > limit:
            piece = piece[: max(200, limit - used)] + "\n... (cut)"
        pieces.append(piece)
        used += len(piece)
    return "\n\n".join(pieces), found


def _line_count(repo, path) -> int:
    try:
        return repo._safe(path).read_text(encoding="utf-8", errors="replace").count("\n") + 1
    except (OSError, ValueError):
        return 0


def _clean_path(path) -> str:
    """`./src\\app.py` -> `src/app.py` (never strips `..`: that stays an error)."""
    path = str(path or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _numbered(repo, path, start, end) -> str:
    try:
        lines = repo._safe(path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError, UnicodeDecodeError):
        return "(can't read this file)"
    end = min(end, len(lines))
    more = f"\n... ({len(lines) - end} more lines)" if end < len(lines) else ""
    return redact("\n".join(f"{n:>5} {lines[n - 1][:200]}" for n in range(start, end + 1)) + more)


def current_code(ws, paths, limit=14_000) -> str:
    """The changed files as they are now, so the next edits can match them exactly."""
    out, used = [], 0
    for path in paths:
        text = _numbered(ws.repo, path, 1, 600)
        if used + len(text) > limit:
            text = text[: max(300, limit - used)] + "\n... (cut; ask for a range with need)"
        out.append(f"--- {path} (as it is now) ---\n{text}")
        used += len(text)
    return "\n\n".join(out)


# --- reading the AI's answer and applying it ----------------------------------------------------

def parse_reply(text) -> dict:
    data = extract_object(text)  # raises ValueError when it isn't a JSON object
    clean_edits = [{"path": _clean_path(e.get("path")), "old": str(e.get("old") if e.get("old") is not None else ""),
                    "new": str(e.get("new") if e.get("new") is not None else "")}
                   for e in (data.get("edits") or []) if isinstance(e, dict)]
    clean_files = [{"path": _clean_path(f.get("path")), "content": str(f.get("content") or "")}
                   for f in (data.get("files") or []) if isinstance(f, dict)]
    return {"summary": str(data.get("summary") or ""), "edits": clean_edits[:MAX_EDITS],
            "files": clean_files[:MAX_FILES], "check_command": str(data.get("check_command") or ""),
            "need": [str(n) for n in (data.get("need") or []) if n][:8], "too_big": bool(data.get("too_big"))}


def nearest_text(text, old) -> str:
    """Where the AI probably meant: the lines of the file closest to the first line of what it wrote."""
    def squash(line):
        return re.sub(r"\s+", " ", line.strip())

    wanted = squash(next((ln for ln in old.splitlines() if ln.strip()), ""))
    lines = text.splitlines()
    if not wanted or not lines:
        return ""
    squashed = [squash(ln) for ln in lines]
    # Spacing is the usual slip, so look for the wanted line inside the file's lines first, then by similarity.
    at = next((i for i, ln in enumerate(squashed) if wanted in ln), None)
    if at is None:
        close = difflib.get_close_matches(wanted, squashed, n=1, cutoff=0.5)
        if not close:
            return ""
        at = squashed.index(close[0])
    first, last = max(0, at - 3), min(len(lines), at + 8)
    return "\n".join(f"{n + 1:>5} {lines[n][:200]}" for n in range(first, last))


def apply_changes(ws, edits, files) -> tuple:
    """Check every edit first, then write them all: (paths written, problems). A problem means nothing was written."""
    plan, problems = {}, []
    for f in files:
        try:
            target = ws._target(f["path"])
            if is_secret_file(f["path"]):
                raise ValueError("it may hold secrets, so it can't be written")
            if target.exists() and target.read_text(encoding="utf-8", errors="replace").count("\n") > 80:
                raise ValueError("it already exists and is long: change it with `edits`, don't rewrite it")
            plan[f["path"]] = f["content"]
        except ValueError as exc:
            problems.append(f"{f['path'] or '(no path)'}: {exc}")
    for e in edits:
        path = e["path"]
        try:
            target = ws._target(path)
            if is_secret_file(path):
                raise ValueError("it may hold secrets, so it can't be edited")
            if path in plan:
                text = plan[path]
            elif target.exists():
                text = target.read_text(encoding="utf-8", errors="replace")
            else:
                raise ValueError("it doesn't exist; put a new file in `files` instead")
            for old, new in _edit_variants(e["old"], e["new"]):
                if old and text.count(old) == 1:
                    plan[path] = text.replace(old, new, 1)
                    break
            else:
                count = text.count(e["old"]) if e["old"] else 0
                hint = nearest_text(text, e["old"])
                raise ValueError(("`old` appears %d times: add neighbouring lines to make it unique" % count
                                  if count > 1 else "`old` isn't in the file exactly as written") +
                                 (f". This is what the file really says there (copy `old` from it):\n{hint}" if hint else ""))
        except ValueError as exc:
            problems.append(f"{path or '(no path)'}: {exc}")
    if problems:
        return [], problems
    return _write_all(ws, plan, {f["path"] for f in files})


def _write_all(ws, plan, authored) -> tuple:
    """Write every file, or none: if one write fails, the ones already written are put back as they were.
    (Seen live: a half-applied change made the AI's next edits not match the file any more.)"""
    backup, written = {}, []
    for path in plan:
        target = ws._target(path)
        backup[path] = target.read_bytes() if target.exists() else None
    try:
        for path, text in plan.items():
            ws.write(path, text, limit=path in authored)  # size limit only for what the AI writes from scratch
            written.append(path)
    except (ValueError, OSError) as exc:
        for path in written:
            target = ws._target(path)
            if backup[path] is None:
                target.unlink()
            else:
                target.write_bytes(backup[path])
            ws.changed.discard(path)
        ws.repo.touch()
        return [], [f"writing failed ({exc}); nothing was changed"]
    return written, []


# --- the whole thing --------------------------------------------------------------------------------

def build(llm, task, ws, check="", max_steps=None, fix_rounds=3, language="auto", simple=False, on_event=None,
          budget=None, browser=None, earlier="") -> dict:
    """Do the task in quick mode when it suits, else step by step. Same report as builder.build, plus
    report["mode"] ("quick" or "agent"), ["requests"], ["tokens"] and, after a hand-over, ["escalated"]."""
    event = on_event or (lambda kind, **info: None)
    budget = budget or max(getattr(llm, "context_budget", 9000), 9000)
    meter = Meter()

    def agent(reason=None, attempt=""):
        if reason:
            event("escalate", reason=reason)
        report = builder.build(llm, task, ws, check=check, max_steps=max_steps, fix_rounds=fix_rounds,
                               language=language, simple=simple, on_event=on_event, budget=budget, browser=browser,
                               earlier=(earlier + "\n" if earlier and attempt else earlier) + attempt, meter=meter,
                               continuing=not attempt)
        if reason:
            report["escalated"] = reason
        return report

    if (os.environ.get("TASKMIND_QUICK", "").lower() == "off" or budget < MIN_BUDGET
            or not ws.repo.index_wanted(1) or ws.repo.code_file_count() == 0):
        # switched off, a tiny free tier, the index switched off, or an empty project: step by step
        return agent()

    event("quick", stage="start")
    known_check = check or detect_check(ws.root)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": gather(ws, task, budget, event, language, simple, earlier, known_check)}]
    max_tokens = 8000 if budget < 100_000 else 16_000
    report = {"status": "unfinished", "summary": "", "check": known_check, "check_output": "", "steps": 0,
              "rounds": 0, "mode": "quick"}
    need_rounds = bad_replies = failed_edits = empties = lookups_missed = 0
    attempt = ""
    while meter.requests < MAX_REQUESTS:
        reply = llm.chat(messages, json_mode=True, max_tokens=max_tokens)
        meter.add(reply)
        text = (reply.get("content") or "").strip()
        if not text:  # usually the whole reply went on thinking: give it room
            empties += 1
            if empties > 2:
                return agent("the AI sent empty replies")
            max_tokens = min(max_tokens * 2, 65_536)
            continue
        try:
            data = parse_reply(text)
        except ValueError:
            bad_replies += 1
            if bad_replies > 1:
                return agent("the AI didn't answer in the expected format")
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "That wasn't a JSON object. Reply with ONLY the JSON object."}]
            continue
        messages.append({"role": "assistant", "content": text})
        if data["too_big"]:
            return agent("the AI says this needs step-by-step exploration")
        if not data["edits"] and not data["files"]:
            if not data["need"]:
                return agent("the AI gave no edits")
            event("quick_asked", need=data["need"])
            code, found = resolve_needs(ws, data["need"])
            if found:
                need_rounds += 1
            else:  # nothing it asked for exists: say how to ask, and don't spend a round on it
                lookups_missed += 1
            if need_rounds > MAX_NEED_ROUNDS or lookups_missed > 2:
                return agent("it needed to read more code than quick mode handles")
            messages.append({"role": "user", "content": "Here is what you asked for:\n\n" + code + (
                "" if found else "\n\n" + ASK_FORMS) + "\n\nNow reply with the JSON including your edits."})
            continue
        written, problems = apply_changes(ws, data["edits"], data["files"])
        if problems:
            failed_edits += 1
            event("quick_retry", reason=problems[0].splitlines()[0][:150])
            if failed_edits > 2:
                return agent("the edits could not be applied: " + problems[0].splitlines()[0][:150])
            messages.append({"role": "user", "content": "None of your edits were applied, because:\n- "
                             + "\n- ".join(problems) + "\n\nReply with the corrected JSON (all of the edits again)."})
            continue
        for path in written:
            event("tool", name="write_file", args={"path": path})
        report["summary"] = data["summary"]
        verdict, code, output = builder.run_checks(ws, known_check, data["check_command"], browser, event, report)
        if verdict == "unverified":
            report["status"] = "unverified"
            break
        if verdict == "passed":
            report["status"] = "passed"
            break
        report["rounds"] += 1
        attempt = (f"Quick attempt: changed {', '.join(sorted(ws.changed))}. The project's check "
                   f"`{report['check']}` then failed:\n{output[-1500:]}")
        if report["rounds"] > QUICK_FIX_ROUNDS:
            return agent("the checks still fail after quick fixes", attempt)
        messages.append({"role": "user", "content": (
            f"TaskMind applied your edits and ran `{report['check']}`. It failed (exit code {code}):\n{output}\n\n"
            "The changed files as they are now:\n\n" + current_code(ws, sorted(ws.changed)) +
            "\n\nReply with JSON edits that fix it (`old` must match the code as it is NOW). Fix the code, not the "
            "tests, unless a test is wrong.")})
    else:
        return agent("it took more requests than quick mode allows", attempt or "Quick attempt: nothing applied yet.")

    report["steps"] = meter.requests
    report["requests"], report["tokens"] = meter.requests, meter.tokens
    report["changed"] = sorted(ws.changed)
    report["changed_tests"] = ws.changed_tests()
    return report
