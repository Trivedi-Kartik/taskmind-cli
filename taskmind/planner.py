"""Reminders and calendar commands."""

import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from taskmind import calendar_export, config, reminders, ui
from taskmind.core import CLIError, get_task, open_db
from taskmind.parsing import parse_ref


def make_speaker():
    from taskmind.cli import make_speaker as speaker  # cli owns voice set-up
    return speaker()


def cmd_remind(args):
    db = open_db()
    cfg = config.load()
    action = args.action or "list"
    if action == "set":
        if not args.id or not args.when:
            raise CLIError("Use: tm remind set <id> <when>, e.g. tm remind set 4 tomorrow 9am")
        task = get_task(db, parse_ref(args.id)[0])
        when = reminders.parse_when(" ".join(args.when))
        if when < datetime.now().strftime("%Y-%m-%d %H:%M"):
            ui.out(f"[yellow]{when} has already passed, so this reminder goes out at the next check.[/yellow]")
        db.update_task(task["id"], remind_at=when)
        db.log(task["id"], "reminder", f"Reminder set for {when}")
        ui.out(f"🔔 TM-{task['id']} reminder set for {when}")
        if not reminders.schedule_enabled():
            ui.out("[dim]Notifications are off; turn them on with `tm remind on`.[/dim]")
    elif action == "check":
        speaker = make_speaker() if args.speak else None
        sent = reminders.check(db, cfg, speaker.say if speaker else None)
        if not args.quiet:
            ui.out("\n".join(f"🔔 {ui.escape(s)}" for s in sent) if sent else "No reminders due right now.")
    elif action == "watch":
        ui.out(f"Watching for reminders every {args.every}s. Leave this running; Ctrl+C to stop.")
        speaker = make_speaker() if args.speak else None
        while True:
            for s in reminders.check(db, config.load(), speaker.say if speaker else None):
                ui.out(f"{datetime.now():%H:%M} 🔔 {ui.escape(s)}")
            time.sleep(args.every)
    elif action == "on":
        cmd = reminders.tm_executable()
        ui.out("This adds a line to your crontab so TaskMind checks for reminders every 5 minutes, "
               "even with the terminal closed.")
        if not args.yes and not ui.confirm("Turn on reminder notifications?", default=True):
            return
        reminders.enable(cmd, speak=args.speak)
        cfg.setdefault("reminders", {})["speak"] = args.speak
        config.save(cfg)
        ok = reminders.notify("TaskMind reminders are on", "You'll get a notification when tasks are due.")
        ui.out("[green]Reminders are on.[/green]" + ("" if ok else
               " [yellow]No desktop notifier found; reminders will be logged only.[/yellow]"))
    elif action == "off":
        reminders.disable()
        ui.out("Reminders are off.")
    else:  # list
        daily = (cfg.get("reminders") or {}).get("time") or reminders.DEFAULT_TIME
        status = "[green]on[/green]" if reminders.schedule_enabled() else "off (`tm remind on`)"
        ui.out(f"Notifications: {status}   Due-date reminders at {daily}")
        rows = [[f"TM-{t['id']}", t["remind_at"] or "", t.get("due") or "", t["title"]]
                for t in db.list_tasks() if t.get("remind_at") or t.get("due")]
        rows.sort(key=lambda r: (r[1] or r[2] + " 99"))
        if rows:
            ui.table(["ID", "Reminder", "Due", "Title"], rows)
        else:
            ui.out("No reminders or due dates yet. Try: tm remind set 3 tomorrow 9am")


def cmd_calendar(args):
    db = open_db()
    if args.action == "link":
        if not args.id:
            raise CLIError("Use: tm calendar link <id>")
        task = get_task(db, args.id)
        links = calendar_export.google_links(task, db.subtasks(task["id"]))
        if not links:
            raise CLIError(f"TM-{task['id']} has no due date or reminder. Add one with "
                           f"`tm edit {task['id']} --due friday` or `tm remind set {task['id']} tomorrow 9am`.")
        for label, url in links:
            ui.out(f"Add the {label} to Google Calendar:")
            ui.plain(f"  {url}")
            if args.open:
                webbrowser.open(url)
        return
    tasks = db.list_tasks(include_done=True)
    if not args.all:
        # Keep tasks finished in the last 30 days, marked cancelled, so calendars can drop them.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        tasks = [t for t in tasks if t["status"] != "done" or t["updated_at"] >= cutoff]
    items = [(t, db.subtasks(t["id"])) for t in tasks if t.get("due") or t.get("remind_at")]
    if not items:
        raise CLIError("No tasks with a due date or reminder to export.")
    out = Path(args.out).expanduser() if args.out else config.home() / "taskmind.ics"
    with open(out, "w", encoding="utf-8", newline="") as fh:  # keep the \r\n line ends .ics needs
        fh.write(calendar_export.to_ics(items))
    count = sum(len(calendar_export.events(t, s)) for t, s in items)
    ui.out(f"Exported {count} event(s) from {len(items)} task(s) to [bold]{ui.escape(str(out))}[/bold]")
    ui.out("Import it into your calendar:\n"
           "  Google Calendar: Settings → Import & export → Import\n"
           "  Apple Calendar:  File → Import\n"
           "  Outlook:         Add calendar → Upload from file\n"
           "[dim]Export again after changes. Apple Calendar and Outlook update events with the same ID; "
           "Google Calendar's import only adds new ones, so use `tm calendar link` for single changes there.[/dim]")
