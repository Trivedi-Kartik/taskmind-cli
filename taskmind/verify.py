"""Stage 9: check the code that was written against the task's plan.

The checklist (acceptance criteria, edge cases, security risks, test cases) comes
from earlier stages, so every change is held to what was agreed up front, no
matter which AI or person wrote the code.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from taskmind.llm import ask_json
from taskmind.repo_tools import SKIP_DIRS, SKIP_SUFFIXES, Repo
from taskmind.secrets import is_secret_file, redact
from taskmind.stages import PERSONA, describe

STATUSES = ("done", "partial", "missing", "unclear")
# Free tiers allow ~8k tokens per minute, including the reply; keep the whole
# prompt to roughly 4.5k tokens.
PROMPT_BUDGET = 16000
MIN_DIFF_BUDGET = 4000
UNTRACKED_MAX_BYTES = 50_000


class NothingToVerify(Exception):
    pass


def checklist(task) -> list:
    analysis = task.get("analysis") or {}
    items = []

    def add(prefix, kind, texts):
        for i, text in enumerate(texts, start=1):
            items.append({"id": f"{prefix}{i}", "kind": kind, "text": text})

    add("A", "acceptance", task.get("acceptance") or [])
    add("E", "edge case", analysis.get("edge_cases") or [])
    add("S", "security", [f"{s['check']}: {s['note']}" for s in analysis.get("security") or []
                          if s["status"] == "risk"])
    add("T", "test", task.get("tests") or [])
    return items


def _skippable(path) -> bool:
    return (any(path.endswith(s) for s in SKIP_SUFFIXES) or any(p in SKIP_DIRS for p in Path(path).parts)
            or is_secret_file(path))


def _untracked_as_diff(repo: Repo) -> str:
    """New files git does not know about yet, shown as additions."""
    out = []
    for name in repo._git("ls-files", "-z", "--others", "--exclude-standard").split("\0"):
        path = repo.root / name
        if not name or _skippable(name) or not path.is_file() or path.stat().st_size > UNTRACKED_MAX_BYTES:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        out.append(f"diff --git a/{name} b/{name}\nnew file (untracked)\n+++ b/{name}\n"
                   + "\n".join("+" + line for line in lines))
    return "\n".join(out)


def collect_changes(repo: Repo, task_id, base=None, since=None) -> dict:
    """The task's code changes: commits referencing TM-<id> (or base...HEAD), plus uncommitted work."""
    if not repo.is_git:
        raise NothingToVerify("Verification needs a git repository.")
    parts = []
    if base:
        parts.append(repo._git("diff", f"{base}...HEAD"))
        log = repo._git("log", "--format=%h %s", f"{base}..HEAD")
        source = f"changes since {base}"
    else:
        # Same rule as tracking: TM-3, TM-3.2 or "TM-3." at a sentence end, but not TM-31 or XTM-3.
        pattern = rf"(^|[^A-Za-z0-9])TM-{int(task_id)}([.][0-9]+)?([^0-9]|$)"
        args = ["log", "--format=%h %s", "--no-merges", "-i", "-E", f"--grep={pattern}"]
        if since:  # ignore older commits that reused the same task number
            args.append(f"--since={since}")
        log = repo._git(*args, check=False)
        for line in reversed(log.splitlines()):
            parts.append(repo._git("show", "--format=", "--patch", line.split()[0]))
        source = f"commits mentioning TM-{task_id}"
    working = repo._git("diff", "HEAD", check=False) + "\n" + _untracked_as_diff(repo)
    if working.strip():
        parts.append(working)
        source += " + uncommitted changes"
    diff = "\n".join(p for p in parts if p.strip())
    if not diff.strip():
        raise NothingToVerify(
            f"No changes found for TM-{task_id}. Commit with `TM-{task_id}.<n>` in the message, "
            "keep your work uncommitted, or use --base <branch>."
        )
    # Secret files are dropped in split_files; secret values in other files are blanked here.
    return {"diff": redact(diff), "commits": log.splitlines(), "source": source}


def split_files(diff) -> dict:
    """Map each changed path to its diff text (a file touched by several commits is merged)."""
    files, current = {}, None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            match = re.search(r" b/(.+)$", line)
            current = match.group(1) if match else line
            files.setdefault(current, [])
        if current is not None:
            files[current].append(line)
    return {path: "\n".join(lines) for path, lines in files.items() if not _skippable(path)}


def fit_diff(files: dict, important, budget) -> tuple:
    """Pick what fits the token budget: planned files first, then tests, then the rest."""
    important = set(important)

    def rank(path):
        return (0 if path in important else 1 if "test" in path.lower() else 2, path)

    chunks, omitted, left = [], [], budget
    for path in sorted(files, key=rank):
        text = files[path]
        if len(text) <= left:
            chunks.append(text)
            left -= len(text)
        elif left >= 1500:
            chunks.append(text[:left - 100] + "\n... (rest of this file's diff truncated)")
            left = 0
        else:
            omitted.append(path)
    return "\n".join(chunks), omitted


def run(llm, task, subtasks, changes) -> dict:
    items = checklist(task)
    if not items:
        raise NothingToVerify(f"TM-{task['id']} has no plan to check against. Run `tm go {task['id']}` first.")
    analysis = task.get("analysis") or {}
    important = [f["path"] for f in analysis.get("affected_files") or []]
    important += [f for s in subtasks for f in s.get("files") or []]

    listing = "\n".join(f"[{i['id']}] ({i['kind']}) {i['text']}" for i in items)
    instructions = (
        "You are reviewing the code changes made for this task. For EACH checklist item, decide "
        "from the diff whether it is done (clearly implemented, or tested for test items), "
        "partial, missing (nothing in the diff handles it), or unclear (cannot tell from the "
        "diff, e.g. the relevant file was left out). Give short evidence: the file and what you "
        "saw, or what is absent. Do not mark anything done without seeing it in the diff.\n"
        "Also list other problems you notice in the diff (bugs, security issues, leftover debug "
        "code, missing error handling), max 8.\n"
        'Reply as JSON: {"summary": "1-2 sentences", "items": [{"id": "A1", "status": '
        '"done|partial|missing|unclear", "evidence": "..."}], "other_issues": ["..."]}'
    )
    header = f"{describe(task)}\n\nChecklist:\n{listing}\n\n{instructions}"
    budget = max(PROMPT_BUDGET, getattr(llm, "context_budget", 0) // 2)  # roomier on paid providers
    diff_text, omitted = fit_diff(split_files(changes["diff"]), important,
                                  max(MIN_DIFF_BUDGET, budget - len(header)))
    note = ""
    if omitted:
        note = ("\n\nThese changed files were too large to include, so mark items that depend on "
                "them as unclear: " + ", ".join(omitted))
    data = ask_json(llm, PERSONA, f"{header}{note}\n\nCommits:\n" + "\n".join(changes["commits"][:20])
                    + f"\n\nDiff:\n{diff_text}")

    verdicts = {}
    for entry in data.get("items") or []:
        if isinstance(entry, dict) and entry.get("id"):
            verdicts[str(entry["id"]).strip("[] ").upper()] = entry
    results = []
    for item in items:
        verdict = verdicts.get(item["id"], {})
        status = str(verdict.get("status") or "").lower().strip()
        results.append({
            **item,
            "status": status if status in STATUSES else "unclear",
            "evidence": str(verdict.get("evidence") or "").strip() or "Not assessed by the model.",
        })
    counts = {s: sum(r["status"] == s for r in results) for s in STATUSES}
    issues = data.get("other_issues") if isinstance(data.get("other_issues"), list) else []
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": changes["source"],
        "summary": str(data.get("summary") or "").strip(),
        "items": results,
        "other_issues": [str(i).strip() for i in issues if str(i).strip()][:8],
        "omitted": omitted,
        "counts": counts,
    }
