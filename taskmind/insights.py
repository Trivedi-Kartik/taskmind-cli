"""Knowing the user: profile, daily greeting, smart suggestions and the weekly summary.

All of it is computed locally from the task list and activity log, with no AI call,
so it's instant, free and private.
"""

from datetime import date, datetime, timedelta, timezone

LANGUAGES = ("auto", "english", "hindi", "hinglish")


def profile(cfg) -> dict:
    return {"name": "", "language": "auto", "hours": "", "coder": "", **(cfg.get("profile") or {})}


def part_of_day(now=None) -> str:
    hour = (now or datetime.now()).hour
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"


def _days_since(iso) -> int:
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).days


def suggestions(db, cfg, reminders_on=None) -> list:
    """Helpful next actions, most useful first. Each is (text, command)."""
    today = date.today().isoformat()
    out = []
    for t in db.list_tasks(include_done=True):
        tid, steps = t["id"], db.subtasks(t["id"])
        if t["status"] == "done":
            if t.get("repo") and steps and not t.get("verification") and _days_since(t["updated_at"]) <= 14:
                out.append((3, f"TM-{tid} is finished but its code was never checked", f"tm verify {tid}"))
            continue
        if t.get("due") and t["due"] < today:
            days = (date.today() - date.fromisoformat(t["due"])).days
            out.append((1, f"TM-{tid} \"{t['title']}\" is {days} day{'s' if days != 1 else ''} overdue",
                        f"tm edit {tid} --due +2d  (or finish it)"))
        last = db.events(tid, limit=1)
        idle = _days_since(last[0]["at"]) if last else _days_since(t["created_at"])
        if t["status"] == "in_progress" and idle >= 5:
            out.append((2, f"TM-{tid} has had no progress for {idle} days; smaller steps help",
                        f'tm step add {tid} "..."'))
        elif t["status"] == "inbox" and idle >= 2:
            out.append((4, f"TM-{tid} was added {idle} days ago but not thought through yet", f"tm go {tid}"))
        elif t["status"] == "planned" and steps and not any(s["done"] for s in steps) and idle >= 3:
            nxt = steps[0]
            out.append((5, f"TM-{tid} is planned but not started; first step: {nxt['title']}", f"tm prompt {tid}.1"
                        if t.get("repo") else f"tm done {tid}.{nxt['position']}  (when it's done)"))
    due_today = [t for t in db.list_tasks() if t.get("due") == today]
    if len(due_today) > 3:
        out.append((2, f"{len(due_today)} tasks are due today; consider moving some", "tm today"))
    if reminders_on is False and any(t.get("remind_at") for t in db.list_tasks()):
        out.append((6, "You have reminders set, but notifications are off", "tm remind on"))
    return [(text, cmd) for _, text, cmd in sorted(out, key=lambda x: x[0])]


def greeting(db, cfg, reminders_on=None) -> str:
    """One short line for the first time you use TaskMind each day."""
    name = profile(cfg)["name"]
    today = date.today().isoformat()
    tasks = db.list_tasks()
    due = sum(1 for t in tasks if t.get("due") == today)
    overdue = sum(1 for t in tasks if t.get("due") and t["due"] < today)
    active = [t for t in tasks if t["status"] == "in_progress"]
    parts = [f"Good {part_of_day()}{', ' + name if name else ''}!"]
    if not tasks:
        parts.append("Your list is empty; add a task with tm add \"...\" or just tm \"...\".")
        return " ".join(parts)
    facts = [f"{due} due today" if due else "", f"{overdue} overdue" if overdue else "",
             f"{len(active)} in progress" if active else ""]
    facts = [f for f in facts if f] or [f"{len(tasks)} open task{'s' if len(tasks) != 1 else ''}"]
    parts.append(" · ".join(facts) + ".")
    tips = suggestions(db, cfg, reminders_on)
    if tips:
        parts.append(f"Tip: {tips[0][0]} → {tips[0][1]}")
    return " ".join(parts)


def week(db, now=None) -> dict:
    """What happened in the last 7 days."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).isoformat(timespec="seconds")
    events = db.events(since=since, limit=5000)
    tasks = {t["id"]: t for t in db.list_tasks(include_done=True)}
    finished = {e["task_id"] for e in events if e["kind"] == "done"} | {
        t["id"] for t in tasks.values() if t["status"] == "done" and t["updated_at"] >= since}
    steps_done = sum(1 for e in events if e["kind"] == "progress" and "done" in e["message"]
                     and "reopened" not in e["message"])
    today = date.today().isoformat()
    return {
        "added": sum(1 for e in events if e["kind"] == "created"),
        "finished": [tasks[i] for i in sorted(finished) if i in tasks],
        "steps_done": steps_done,
        "verified": sum(1 for e in events if e["kind"] == "verified"),
        "planned": sum(1 for e in events if e["kind"] == "planned"),
        "slipping": [t for t in tasks.values() if t["status"] != "done" and t.get("due") and t["due"] < today],
        "open": sum(1 for t in tasks.values() if t["status"] != "done"),
        "busiest_day": _busiest_day(events),
    }


def _busiest_day(events):
    counts = {}
    for e in events:
        try:
            day = datetime.fromisoformat(e["at"]).astimezone().strftime("%A")
        except ValueError:
            continue
        counts[day] = counts.get(day, 0) + 1
    return max(counts, key=counts.get) if counts else None


def plan_preferences(db) -> dict:
    """What the user changes in AI plans (only edits made after an AI plan count)."""
    rows = db.conn.execute(
        "SELECT e.message FROM events e JOIN tasks t ON t.id = e.task_id "
        "WHERE e.kind = 'plan' AND t.planned_at IS NOT NULL AND e.at >= t.planned_at ORDER BY e.id").fetchall()
    added, removed = [], []
    for (message,) in rows:
        if " added: " in message:
            added.append(message.split(" added: ", 1)[1])
        elif " removed: " in message:
            removed.append(message.split(" removed: ", 1)[1])
    planned = db.conn.execute("SELECT COUNT(*) FROM tasks WHERE planned_at IS NOT NULL").fetchone()[0]
    ai_steps = db.conn.execute(
        "SELECT COUNT(*) FROM subtasks s JOIN tasks t ON t.id = s.task_id WHERE t.planned_at IS NOT NULL").fetchone()[0]
    return {"plans": planned, "ai_steps": ai_steps, "added": added, "removed": removed}


def preference_guidance(db) -> str:
    """Plain instructions for the planner, or "" until there's enough to go on."""
    p = plan_preferences(db)
    lines = []
    if len(p["removed"]) >= 3 and p["ai_steps"] and len(p["removed"]) / p["ai_steps"] > 0.2:
        lines.append("This user often removes steps from your plans: prefer fewer, larger steps.")
    if len(p["added"]) >= 2:
        recent = "; ".join(dict.fromkeys(p["added"][-6:]))
        lines.append(f"Steps this user has added to your plans before (include similar ones when they fit): {recent}.")
    if len(p["removed"]) >= 2:
        recent = "; ".join(dict.fromkeys(p["removed"][-4:]))
        lines.append(f"Steps this user removed before (leave out similar ones unless clearly needed): {recent}.")
    return " ".join(lines)
