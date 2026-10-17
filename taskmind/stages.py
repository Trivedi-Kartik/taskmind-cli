"""The thinking stages of a task: understand, analyze, plan, hand off, follow up."""

import json
import re
from datetime import date, datetime

from taskmind.agent import run_tool_agent
from taskmind.llm import ToolCallError, ask_json, extract_object
from taskmind.repo_tools import Repo

PERSONA = (
    "You are TaskMind, a senior engineer and meticulous planner who helps one developer "
    "manage their own work. You are concrete, brief and practical. You notice edge cases, "
    "hidden dependencies and risks that others miss. Never invent files or APIs you have not seen."
)
GENERAL_PERSONA = (
    "You are TaskMind, a calm, practical personal assistant who helps one person plan and "
    "finish everyday tasks: errands, events, admin, learning, travel, work that isn't code. "
    "You are concrete and brief, use plain language with no jargon, think of what people "
    "usually forget (documents, bookings, deadlines, people to tell, money, time to travel), "
    "and never invent facts you were not given."
)


def persona(task) -> str:
    """Engineering persona for tasks linked to code, a plain-language planner for everything else."""
    return PERSONA if task.get("repo") else GENERAL_PERSONA


def _strs(value, limit=20) -> list:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _num(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def describe(task) -> str:
    parts = [f"Title: {task['title']}"]
    if task.get("description"):
        parts.append(f"Description:\n{task['description']}")
    if task.get("definition"):
        parts.append(f"Agreed definition:\n{task['definition']}")
    if task.get("acceptance"):
        parts.append("Acceptance criteria:\n" + "\n".join(f"- {a}" for a in task["acceptance"]))
    if task.get("clarifications"):
        qa = "\n".join(f"Q: {c['q']}\nA: {c['a']}" for c in task["clarifications"] if c.get("a"))
        if qa:
            parts.append("Clarifications from the developer:\n" + qa)
    return "\n\n".join(parts)


# Stage 1: Capture (by voice)

def parse_spoken_task(llm, transcript, now) -> dict:
    """Turn "add rate limiting to login, high priority, due Friday, remind me tomorrow at 9"
    into task fields. `now` is "YYYY-MM-DD" or "YYYY-MM-DD HH:MM" (local time)."""
    day = date.fromisoformat(now[:10])
    data = ask_json(
        llm,
        GENERAL_PERSONA,
        f"Right now it is {now} ({day.strftime('%A')}).\n"
        f"The user said this to create a task:\n\"{transcript}\"\n\n"
        "Extract the task. The title is a short imperative phrase (max 12 words) in the language "
        "they spoke; put any extra detail in the description. Only set due if they gave a date or "
        "day (convert it to YYYY-MM-DD), priority only if they said it, and remind_at only if they "
        "asked to be reminded (convert it to YYYY-MM-DD HH:MM, 24-hour; a day without a time means "
        "09:00). Reply as JSON: {\"title\": \"...\", \"description\": \"\", \"due\": \"YYYY-MM-DD or null\", "
        '"priority": "high|medium|low|null", "remind_at": "YYYY-MM-DD HH:MM or null"}',
        max_tokens=1000,
    )
    title = str(data.get("title") or "").strip() or transcript.strip()
    due = str(data.get("due") or "").strip()
    remind = str(data.get("remind_at") or "").strip().replace("T", " ")[:16]
    priority = str(data.get("priority") or "").lower().strip()
    return {
        "title": title[:200],
        "description": str(data.get("description") or "").strip(),
        "due": due if _valid_time(due, "%Y-%m-%d") else None,
        "priority": priority if priority in ("high", "medium", "low") else "medium",
        "remind_at": remind if _valid_time(remind, "%Y-%m-%d %H:%M") else None,
    }


def _valid_time(text, fmt) -> bool:
    """Real dates only: rejects things like 2026-02-30 or 25:99 that a model may produce."""
    try:
        datetime.strptime(text, fmt)
        return True
    except ValueError:
        return False


# Stage 2: Understand

def clarify_questions(llm, task, repo_overview=None) -> list:
    context = f"\n\nRepository overview:\n{repo_overview[:3000]}" if repo_overview else ""
    data = ask_json(
        llm,
        persona(task),
        f"{describe(task)}{context}\n\n"
        "Before any work starts, what must be clarified? Ask only questions whose answers would "
        "change the implementation or scope (max 5, most important first). If the task is already "
        'clear, return an empty list. Reply as JSON: {"questions": ["..."]}',
    )
    return _strs(data.get("questions"), limit=5)


def write_definition(llm, task, repo_overview=None) -> dict:
    context = f"\n\nRepository overview:\n{repo_overview[:3000]}" if repo_overview else ""
    # Leave out any previous definition so a redo starts fresh instead of copying it.
    fresh = {k: v for k, v in task.items() if k not in ("definition", "acceptance")}
    data = ask_json(
        llm,
        persona(task),
        f"{describe(fresh)}{context}\n\n"
        "Write a clear task definition the developer can work from: goal, scope, what is out of "
        "scope, and assumptions. Then list testable acceptance criteria. Do not assume languages, "
        "frameworks, fields or files you have not been shown; stay implementation-neutral where "
        "you are unsure (the code analysis comes later). Reply as JSON: "
        '{"definition": "markdown text", "acceptance_criteria": ["..."]}',
    )
    return {
        "definition": str(data.get("definition") or "").strip(),
        "acceptance": _strs(data.get("acceptance_criteria")),
    }


# Stage 3: Analyze

ANALYSIS_FORMAT = (
    '{"summary": "2-3 sentences", '
    '"affected_files": [{"path": "file path", "reason": "why it changes or is impacted"}], '
    '"edge_cases": ["..."], "risks": ["..."], "dependencies": ["..."], "open_questions": ["..."]}'
)

# Every code task is checked against the same list, whatever model is used: the
# process, not the model, decides whether security gets looked at.
SECURITY_CHECKS = [
    "Input validation and injection (SQL, shell, file paths, HTML/templates, CSV/spreadsheet formulas)",
    "Authentication and authorization (who is allowed to do this?)",
    "Secrets and credentials (keys, tokens, passwords)",
    "Sensitive data exposure (logs, error messages, output, API responses)",
    "Error handling and failure modes (partial failures, retries, timeouts)",
    "Dependencies and external calls (new packages, network, untrusted data)",
    "Backwards compatibility and data migration",
]
SECURITY_STATUSES = ("risk", "ok", "n/a")
SECURITY_FORMAT = (
    '"security": {"1": {"status": "risk|ok|n/a", "note": "concrete reason, with file if known"}, '
    f'... one entry for each of the {len(SECURITY_CHECKS)} checks}}'
)


def security_instructions() -> str:
    checks = "\n".join(f"{i}. {c}" for i, c in enumerate(SECURITY_CHECKS, start=1))
    return (
        "Security and safety review: for EVERY check below, decide if this change creates a risk "
        "that must be handled (risk), is already safe (ok), or does not apply (n/a). Mark ok only "
        "when existing code already guarantees safety with no work in this task. If the new code "
        "must do something to stay safe (for example check the login, escape output, validate "
        "input), that is a risk, and the note says what must be done. Be specific to this task "
        f"and code, not generic.\n{checks}"
    )


# An "ok" whose note asks for work ("replicate the same check", "must escape") is really a
# risk. Models make this slip often, so the rule is enforced here, not left to the model.
_ACTION_NEEDED = re.compile(
    r"\b(must|should|ensure|need(s|ed)? to|replicate|re-?use the|apply the|"
    r"require[sd]?|make sure|remember to|don'?t forget|has to|have to|same check)\b", re.I)


def _normalize_security(raw) -> list:
    """One entry per check, in order; anything the model skipped is marked unchecked."""
    if isinstance(raw, list):
        raw = {str(i): v for i, v in enumerate(raw, start=1)}
    if not isinstance(raw, dict):
        raw = {}
    result = []
    for i, check in enumerate(SECURITY_CHECKS, start=1):
        entry = raw.get(str(i)) or raw.get(i)
        entry = entry if isinstance(entry, dict) else {}
        status = str(entry.get("status") or "").lower().strip()
        status = status if status in SECURITY_STATUSES else "unchecked"
        note = str(entry.get("note") or "").strip()
        if status == "ok" and _ACTION_NEEDED.search(note):
            status = "risk"
            note = f"{note} (marked risk: the new code has to do this)"
        result.append({"check": check, "status": status, "note": note})
    return result


def _normalize_analysis(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Analysis is not a JSON object")
    raw_files = data.get("affected_files") or []
    if isinstance(raw_files, (str, dict)):  # e.g. "none" or a single object
        raw_files = [] if isinstance(raw_files, str) and raw_files.lower() in ("none", "n/a", "") else [raw_files]
    files = []
    for item in raw_files:
        if isinstance(item, dict) and item.get("path"):
            files.append({"path": str(item["path"]), "reason": str(item.get("reason", ""))})
        elif isinstance(item, str):
            files.append({"path": item, "reason": ""})
    return {
        "summary": str(data.get("summary") or "").strip(),
        "affected_files": files[:30],
        "edge_cases": _strs(data.get("edge_cases")),
        "risks": _strs(data.get("risks")),
        "dependencies": _strs(data.get("dependencies")),
        "open_questions": _strs(data.get("open_questions")),
        "security": _normalize_security(data.get("security")) if "security" in data else [],
    }


def _parses(text) -> bool:
    try:
        return isinstance(extract_object(text), dict)
    except ValueError:
        return False


def analyze(llm, task, repo: Repo = None, on_step=None) -> dict:
    if repo is None:
        data = ask_json(
            llm,
            GENERAL_PERSONA,
            f"{describe(task)}\n\nThis is an everyday task, not a coding task. Think it through: "
            "what is needed (things, documents, people, information, money, time), what could block "
            "it, what people usually forget, and what could go wrong. Put those in edge_cases and "
            "risks in plain language; anything needed from others in dependencies. Leave "
            f"affected_files empty. Reply as JSON: {ANALYSIS_FORMAT}",
        )
        return _normalize_analysis(data)

    goal = (
        f"{describe(task)}\n\nFind everything in the repository this task touches: files to "
        "change, callers and dependents of that code, config, migrations, tests and docs. "
        "Then list edge cases (inputs, states, concurrency, errors, permissions, backwards "
        "compatibility) and risks, including ones the developer probably has not thought of.\n\n"
        f"{security_instructions()}\n\n"
        f"Final answer: only this JSON, no prose:\n{ANALYSIS_FORMAT[:-1]}, {SECURITY_FORMAT}}}"
    )
    explore_hint = (
        "Investigate with your tools before answering: search for the key names, then read the "
        "relevant code. Be economical: you have a small number of tool calls."
    )
    try:
        text = run_tool_agent(llm, PERSONA, goal, repo, explore_hint, on_step=on_step, accept=_parses)
        result = _normalize_analysis(extract_object(text))
    except (ToolCallError, ValueError):
        # Some free models stumble on tool calling; fall back to a single call with
        # a repo overview plus searches for words from the task title.
        if on_step:
            on_step("fallback", {"reason": "tool calling failed, using a repo snapshot"})
        snapshot = repo.overview()
        for word in {w for w in task["title"].replace("/", " ").split() if len(w) > 3}:
            snapshot += f"\n\nSearch '{word}':\n" + repo.search(word, max_results=8)
        data = ask_json(
            llm,
            PERSONA,
            f"{goal}\n\nNo tools are available; use this repository snapshot:\n{snapshot[:12000]}",
        )
        result = _normalize_analysis(data)
    if not result["security"]:
        result["security"] = _normalize_security({})  # model ignored it: all unchecked, never silently skipped
    return result


# Stage 4: Plan

def plan(llm, task, analysis, preferences="") -> dict:
    learned = f"What this user prefers (learned from how they edit plans): {preferences}\n\n" if preferences else ""
    if not task.get("repo"):
        return _plan_everyday(llm, task, analysis, learned)
    data = ask_json(
        llm,
        PERSONA,
        f"{describe(task)}\n\nAnalysis:\n{json.dumps(analysis, indent=1)[:6000]}\n\n{learned}"
        "Break this into an ordered plan of small subtasks (each roughly 0.5-4 hours, each "
        "independently committable). Include writing tests as part of the relevant subtasks. "
        "The analysis comes from reading the real code: where it conflicts with assumptions in "
        "the definition (language, libraries, fields), follow the analysis. "
        "Every security item with status \"risk\" must be handled by a subtask and covered by a "
        "test case. "
        "Also list concrete test cases covering the edge cases, and set the overall priority. "
        'Reply as JSON: {"priority": "high|medium|low", "estimate_hours": number, '
        '"subtasks": [{"title": "...", "details": "what exactly to do", "files": ["..."], '
        '"estimate_hours": number}], "tests": ["..."]}',
        max_tokens=4000,
    )
    return _normalize_plan(data)


def _plan_everyday(llm, task, analysis, learned="") -> dict:
    data = ask_json(
        llm,
        GENERAL_PERSONA,
        f"{describe(task)}\n\nThings to consider:\n{json.dumps(analysis, indent=1)[:4000]}\n\n{learned}"
        "Break this into an ordered list of concrete steps. Each step is one action someone can do "
        "in a single sitting (roughly 15 minutes to 2 hours), starts with a verb, and says exactly "
        "what to do (who to contact, what to book, buy, write or check). Put steps with deadlines or "
        "long waits (bookings, approvals, deliveries) early. Under tests, list 2-5 simple checks "
        "that show the task is really done. Set the overall priority. Reply as JSON: "
        '{"priority": "high|medium|low", "estimate_hours": number, "subtasks": [{"title": "...", '
        '"details": "what exactly to do", "estimate_hours": number}], "tests": ["..."]}',
        max_tokens=3000,
    )
    return _normalize_plan(data)


def _normalize_plan(data) -> dict:
    subtasks = []
    for item in data.get("subtasks") or []:
        if isinstance(item, dict) and item.get("title"):
            subtasks.append({
                "title": str(item["title"]).strip(),
                "details": str(item.get("details") or "").strip(),
                "files": _strs(item.get("files")),
                "estimate_hours": _num(item.get("estimate_hours")),
            })
    priority = str(data.get("priority") or "medium").lower()
    estimate = _num(data.get("estimate_hours"))
    if estimate is None:
        estimate = round(sum(s["estimate_hours"] or 0 for s in subtasks), 1) or None
    return {
        "priority": priority if priority in ("high", "medium", "low") else "medium",
        "estimate_hours": estimate,
        "subtasks": subtasks[:15],
        "tests": _strs(data.get("tests"), limit=25),
    }


# Stage 6: Execute (hand-off to a coding agent)

def handoff_prompt(task, subtasks, position=None) -> str:
    """Build a self-contained prompt for Claude Code, Cursor or any coding agent. No LLM call."""
    analysis = task.get("analysis") or {}
    lines = [f"# Task TM-{task['id']}: {task['title']}", ""]
    lines.append(task.get("definition") or task.get("description") or "")
    if task.get("acceptance"):
        lines += ["", "## Acceptance criteria"] + [f"- {a}" for a in task["acceptance"]]
    if analysis.get("affected_files"):
        lines += ["", "## Affected files (from analysis, verify before editing)"]
        lines += [f"- `{f['path']}`: {f['reason']}" for f in analysis["affected_files"]]
    if analysis.get("edge_cases"):
        lines += ["", "## Edge cases to handle"] + [f"- {e}" for e in analysis["edge_cases"]]
    if analysis.get("risks"):
        lines += ["", "## Risks"] + [f"- {r}" for r in analysis["risks"]]
    security = analysis.get("security") or []
    risky = [s for s in security if s["status"] == "risk"]
    unchecked = [s for s in security if s["status"] == "unchecked"]
    if risky or unchecked:
        lines += ["", "## Security requirements (must be handled and tested)"]
        lines += [f"- {s['check']}: {s['note']}" for s in risky]
        lines += [f"- {s['check']}: not assessed yet, check it yourself" for s in unchecked]
    if subtasks:
        lines += ["", "## Plan"]
        for s in subtasks:
            mark = "x" if s["done"] else " "
            focus = "  <-- DO THIS ONE NOW" if position == s["position"] else ""
            lines.append(f"- [{mark}] TM-{task['id']}.{s['position']} {s['title']}{focus}")
            if s.get("details") and (position is None or position == s["position"]):
                lines.append(f"      {s['details']}")
    if task.get("tests"):
        lines += ["", "## Test cases"] + [f"- {t}" for t in task["tests"]]
    ref = f"TM-{task['id']}.{position}" if position else f"TM-{task['id']}.<n>"
    scope = f"Only implement subtask TM-{task['id']}.{position}." if position else (
        "Work through the unfinished subtasks in order.")
    lines += [
        "", "## Instructions",
        f"{scope} Read the relevant code first and confirm the plan still fits it. "
        "Write or update tests for the edge cases and security requirements above.",
        f"Put `{ref}` in each commit message so TaskMind can track progress automatically.",
    ]
    return "\n".join(lines).strip() + "\n"


# Stage 7: Track (optional AI matching of commits without TM- references)

def match_commits(llm, task, subtasks, commits) -> list:
    open_subtasks = [s for s in subtasks if not s["done"]]
    if not open_subtasks or not commits:
        return []
    data = ask_json(
        llm,
        persona(task),
        f"Task: {task['title']}\n\nOpen subtasks:\n"
        + "\n".join(f"{s['position']}. {s['title']}" for s in open_subtasks)
        + "\n\nRecent commits:\n"
        + "\n".join(f"{c['sha'][:10]} {c['subject']}" for c in commits[:40])
        + "\n\nWhich commits clearly complete one of the open subtasks? Only include confident "
        'matches. Reply as JSON: {"matches": [{"sha": "first 10 chars", "subtask": number}]}',
    )
    valid = {s["position"] for s in open_subtasks}
    matches = []
    for m in data.get("matches") or []:
        try:
            pos = int(m["subtask"])
        except (KeyError, TypeError, ValueError):
            continue
        if pos in valid and m.get("sha"):
            matches.append({"sha": str(m["sha"]), "subtask": pos})
    return matches


# Help: answering questions about TaskMind and the user's own tasks

HELP_PERSONA = (
    "You are TaskMind's built-in help. TaskMind is a command-line task agent; every command starts "
    "with `tm`. Answer the user's question about using TaskMind, about their own tasks, or about the "
    "basics it relies on (API keys, AI providers, git commits, the terminal). Use only the command "
    "reference, guide excerpts and the user's current state you are given; if they don't cover the "
    "question, say so and suggest `tm help` or `tm tutorial`. Give the exact commands to type, in "
    "backticks, using only commands and options that appear in the command reference, written exactly "
    "as there (never invent flags, time formats or model names). To connect an AI provider or add a key, "
    "recommend `tm setup` (guided); to see model names, `tm models`. Use plain language a beginner "
    "understands, and keep it short (at most about 10 lines). Answer in the language of the question "
    "itself: an English question gets an English answer, even if other text you see is in another "
    "language; a question in Hinglish (Hindi in Latin letters) gets Hinglish in Latin letters, not "
    "Devanagari. If the question has nothing "
    "to do with TaskMind or the user's work, answer it in one or two sentences, then mention what "
    "TaskMind can help with. Never ask for or repeat an API key."
)


def answer_question(llm, question, guide, state, commands="") -> str:
    messages = [
        {"role": "system", "content": HELP_PERSONA},
        {"role": "user", "content": (
            f"Command reference (every command and option that exists):\n{commands[:4000]}\n\n"
            f"Guide excerpts:\n{guide[:4500]}\n\nThe user's current state:\n{state[:2000]}\n\n"
            f"Question: {question}"
        )},
    ]
    return (llm.chat(messages, max_tokens=1500).get("content") or "").strip()


# The "brain": turn a request in any words into TaskMind's own commands

BRAIN_PERSONA = (
    "You are TaskMind's assistant inside a command-line task manager. The user tells you, in any "
    "language, what they want. You never act yourself: you choose TaskMind commands, and TaskMind shows "
    "them to the user and runs them only after the user agrees. Decide on one type:\n"
    "- actions: they want something done that the commands in the reference can do. List the exact "
    "commands in order, as argument lists without the leading \"tm\" (for example "
    "[\"remind\", \"set\", \"1\", \"tomorrow\", \"9am\"]). Use only commands and options from the "
    "reference, written exactly as there; never invent options, time formats or task numbers. Refer to "
    "tasks by their number from the task list; when they describe a task in words, pick the matching "
    "number. For a task created by an earlier \"add\" in your own list, write NEW as its number (NEW, or "
    "NEW.2 for its step 2). Keep it to what they asked for; add -d details only if they gave some. "
    "Questions that a viewing command answers directly (what to work on today -> today, show my tasks -> "
    "list, what's in task 3 -> show 3, my reminders -> remind list) are actions with that command.\n"
    "- answer: they asked a question. Answer it briefly from the reference and their state; you may "
    "suggest commands.\n"
    "- question: you can't tell which task they mean or what they want. Ask one short question.\n"
    "- unsupported: they want something TaskMind cannot do (send emails or messages, buy things, browse "
    "the web, edit code, anything not in the reference). Say so honestly and briefly, and suggest the "
    "closest thing TaskMind can do, if any.\n"
    "Write \"message\" in the language of the request itself: an English request gets English, even if "
    "task titles or other text are in another language; Hinglish (Hindi in Latin letters) gets Hinglish "
    "in Latin letters. For actions, the message says what you are about to do (it hasn't happened yet). "
    "Command arguments stay as in the reference. Never include API keys. Reply as JSON: "
    '{"type": "actions|answer|question|unsupported", "message": "short text for the user", '
    '"actions": [{"argv": ["..."], "why": "short reason"}]}'
)
BRAIN_TYPES = ("actions", "answer", "question", "unsupported")


def brain(llm, request, reference, state, now) -> dict:
    data = ask_json(
        llm,
        BRAIN_PERSONA,
        f"Right now it is {now}.\n\nCommand reference (the only commands you may use):\n{reference[:4000]}"
        f"\n\nThe user's current state:\n{state[:2500]}\n\nRequest: {request}",
        max_tokens=2000,
    )
    kind = str(data.get("type") or "").lower().strip()
    actions = []
    for item in data.get("actions") or []:
        if isinstance(item, dict):
            argv = item.get("argv")
            if isinstance(argv, str):  # "tm list" written as one string
                argv = argv.split()
        elif isinstance(item, list):
            argv = item
        else:
            continue  # a bare word is not a command
        if isinstance(argv, list) and argv:
            argv = [str(a) for a in argv]
            if argv[0] == "tm":
                argv = argv[1:]
            if argv:
                actions.append({"argv": argv, "why": str(item.get("why") or "") if isinstance(item, dict) else ""})
    if kind not in BRAIN_TYPES:
        kind = "actions" if actions else "answer"
    return {"type": kind, "message": str(data.get("message") or "").strip(), "actions": actions[:8]}


# Stage 8: Follow up

def briefing(llm, digest) -> str:
    messages = [
        {"role": "system", "content": GENERAL_PERSONA},
        {"role": "user", "content": (
            f"Here is the person's task status:\n{digest}\n\n"
            "Write a short morning briefing (max 8 lines): what to focus on first and why, "
            "anything overdue or blocked, and one concrete next action. Plain text."
        )},
    ]
    return (llm.chat(messages, max_tokens=1500).get("content") or "").strip()
