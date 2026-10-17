"""How results look on screen: plans, analyses, verifications, settings, summaries."""

import re
from datetime import datetime

from taskmind import config, ui
from taskmind.core import progress


SECURITY_ICON = {"risk": "⚠️ risk", "ok": "✅ ok", "n/a": "➖ n/a", "unchecked": "❔ unchecked"}


VERIFY_ICON = {"done": "✅ done", "partial": "🟡 partial", "missing": "❌ missing", "unclear": "❔ unclear"}


STATUS_ICON = {"inbox": "📥", "clarified": "💬", "analyzed": "🔍", "planned": "🗺️",
               "in_progress": "🚧", "done": "✅"}


SIMPLE_STATUS = {"inbox": "new", "clarified": "defined", "analyzed": "thought through", "planned": "planned",
                 "in_progress": "in progress", "done": "done"}


def simple_mode():
    return ui.SIMPLE


def status_label(status):
    return f"{STATUS_ICON[status]} {SIMPLE_STATUS[status] if simple_mode() else status}"


def stage_banner(step, title, explanation):
    """Say which stage is running and why, so the output is never a wall of text."""
    label = f"Step {step[0]} of {step[1]} · {title}" if step else title
    ui.out(f"\n[bold magenta]━━ {ui.escape(label)} ━━[/bold magenta]")
    ui.out(f"[dim]{ui.escape(explanation)}[/dim]")


def show_analysis(analysis):
    code = bool(analysis.get("security"))  # only code tasks get a security check
    if analysis.get("summary"):
        ui.heading("Summary")
        ui.plain(analysis["summary"])
    if analysis.get("affected_files"):
        ui.heading("Files this task touches")
        ui.table(["File", "Why"], [[f["path"], f["reason"]] for f in analysis["affected_files"]])
    ui.bullets("Edge cases: situations the code must handle" if code else "Things to watch out for",
               analysis.get("edge_cases"))
    ui.bullets("Risks: what could go wrong", analysis.get("risks"))
    ui.bullets("Depends on", analysis.get("dependencies"))
    ui.bullets("Open questions: worth checking before you start", analysis.get("open_questions"))
    if analysis.get("security"):
        ui.heading("Security check (⚠️ risk = must be handled; the plan includes it)")
        ui.table(["Check", "Status", "Note"],
                 [[s["check"], SECURITY_ICON[s["status"]], s["note"]] for s in analysis["security"]])


def show_verification(result, full=True):
    c = result["counts"]
    ui.heading(f"Verification ({result['at'][:16].replace('T', ' ')}, {result['source']})")
    ui.plain(f"  ✅ {c['done']} done   🟡 {c['partial']} partial   ❌ {c['missing']} missing   "
             f"❔ {c['unclear']} unclear")
    if result.get("reviewed_by"):
        ui.plain(f"  Reviewed by {result['reviewed_by']}")
    if result.get("review_note"):
        ui.out(f"  [yellow]{ui.escape(result['review_note'])}[/yellow]")
    if result.get("summary"):
        ui.plain(f"  {result['summary']}")
    if not full:
        return
    ui.table(["ID", "Status", "Item", "Evidence"],
             [[i["id"], VERIFY_ICON[i["status"]], i["text"], i["evidence"]] for i in result["items"]])
    ui.bullets("Other issues found in the diff", result.get("other_issues"))
    if result.get("omitted"):
        ui.bullets("Too large to review (check these yourself)", result["omitted"])


def show_plan(db, task):
    subs = db.subtasks(task["id"])
    if subs:
        ui.heading(f"Plan ({progress(db, task['id'])} done"
                   + (f", ~{task['estimate_hours']}h" if task.get("estimate_hours") else "") + ")")
        for s in subs:
            mark = "✅" if s["done"] else "⬜"
            est = f" (~{s['estimate_hours']}h)" if s.get("estimate_hours") else ""
            ui.plain(f"  {mark} TM-{task['id']}.{s['position']}  {s['title']}{est}")
            if s.get("details") and not s["done"]:
                ui.plain(f"       {s['details']}")
    ui.bullets("Test cases: how to prove it works" if task.get("repo") else "Done when", task.get("tests"))


def key_status(s):
    if s["api_key"]:
        return "set"
    if not s["key_env"]:
        return "not needed"
    return "from SDK login" if s.get("key_optional") else "MISSING"


def show_settings(cfg):
    s = config.llm_settings(cfg)
    stage_rows = ", ".join(f"{k}={v}" for k, v in sorted(cfg.get("stages", {}).items())) or "none (main provider)"
    coder = cfg.get("coder")
    ui.table(["Setting", "Value"], [
        ["provider", f"{s['provider']} ({s['label']}, {s['cost']})"],
        ["model", s["model"] or "not chosen (run `tm setup`)"],
        ["api key", key_status(s)],
        ["backups (in order)", " → ".join(cfg.get("fallback") or []) or "none"],
        ["per-stage AI", stage_rows],
        ["you code with", config.PROVIDERS[coder]["label"] if coder else "not set"],
        ["verify reviewer", cfg.get("review", "auto")],
        ["mode", cfg.get("mode") or "developer"],
        ["data folder", str(config.home())],
    ])


def show_go_summary(db, task):
    analysis = task.get("analysis") or {}
    steps = db.subtasks(task["id"])
    risks = sum(1 for s in analysis.get("security") or [] if s["status"] == "risk")
    facts = []
    if task.get("repo"):
        facts.append(f"{len(analysis.get('affected_files') or [])} file(s) to change")
        facts.append(f"{len(analysis.get('edge_cases') or [])} edge cases")
        facts.append(f"{risks} security risk(s) to handle")
    facts.append(f"{len(steps)} steps")
    ui.out(f"\n[bold green]✅ TM-{task['id']} is planned:[/bold green] " + ", ".join(facts) + ".")
    tid = task["id"]
    if task.get("repo"):
        ui.out("[bold]What to do next:[/bold]\n"
               f"  1. `tm prompt {tid}.1`: instructions for step 1, ready to paste into Claude Code or Cursor\n"
               f"     (or `tm exec {tid}.1` to start Claude Code with them)\n"
               f"  2. After coding, commit with \"TM-{tid}.1\" in the message, then `tm sync` ticks the step off\n"
               f"  3. When all steps are done, `tm verify {tid}` checks the code against this plan")
    else:
        ui.out("[bold]What to do next:[/bold]\n"
               f"  1. Start with step 1; mark it done with `tm done {tid}.1`\n"
               f"  2. Set a reminder: `tm remind set {tid} tomorrow 9am`\n"
               "  3. Check `tm today` each day to see what's next")


def finished_titles(db, events):
    seen, out = set(), []
    for e in events:
        task = db.get_task(e["task_id"]) if e["task_id"] else None
        if task and task["id"] not in seen:
            seen.add(task["id"])
            out.append(f"TM-{task['id']} {task['title']}")
    return out


def spoken_summary(sections):
    hour = datetime.now().hour
    parts = [f"Good {'morning' if hour < 12 else 'afternoon' if hour < 18 else 'evening'}."]
    for title, items in sections:
        if not items:
            continue
        label = re.sub(r"\(.*?\)", "", title)
        label = re.sub(r"[^\w\s,]", "", label).strip()
        count = f"{len(items)} {'task' if len(items) == 1 else 'tasks'}"
        top = ". ".join(re.sub(r"\[\d+/\d+\]", "", i) for i in items[:3])
        parts.append(f"{label}: {count}. {top}.")
    return " ".join(parts)
