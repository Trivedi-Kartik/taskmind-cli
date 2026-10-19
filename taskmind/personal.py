"""About the user and their data: profile, week, greeting, backups, export, bug reports, feedback."""

import json
import os
import re
import shlex
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from taskmind import __version__, config, diagnostics, insights, reminders, ui
from taskmind.core import CLIError, backups_dir, interactive, open_db
from taskmind.db import DB


def make_speaker():
    from taskmind.cli import make_speaker as speaker  # cli owns voice set-up
    return speaker()


def cmd_me(args):
    cfg = config.load()
    prof = insights.profile(cfg)
    changed = False
    for field in ("name", "language", "hours", "coder"):
        value = getattr(args, field)
        if value is not None:
            if field == "hours" and value and not re.fullmatch(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}", value):
                raise CLIError("--hours takes a range like 09:00-18:00")
            prof[field], changed = value, True
    if not changed and interactive():
        ui.out("[dim]Tell TaskMind a little about you (press Enter to keep the current value).[/dim]")
        prof["name"] = ui.ask(f"What should I call you? [{prof['name'] or 'skip'}]") or prof["name"]
        lang = ui.ask(f"Language for answers: auto, english, hindi or hinglish? [{prof['language']}]").lower()
        prof["language"] = lang if lang in insights.LANGUAGES else prof["language"]
        prof["hours"] = ui.ask(f"Your working hours, e.g. 09:00-18:00? [{prof['hours'] or 'skip'}]") or prof["hours"]
        prof["coder"] = ui.ask(f"Which AI coding tool do you use (Claude Code, Cursor, Codex...)? "
                               f"[{prof['coder'] or 'skip'}]") or prof["coder"]
        changed = True
    if changed:
        cfg["profile"] = prof
        config.save(cfg)
        ui.out("[green]Saved.[/green]")
    ui.table(["About you", ""], [["name", prof["name"] or "-"], ["language", prof["language"]],
                                 ["working hours", prof["hours"] or "-"], ["coding tool", prof["coder"] or "-"]])
    learned = insights.preference_guidance(open_db())
    ui.out(f"[bold]Learned from your plan edits:[/bold] {ui.escape(learned)}" if learned else
           "[dim]As you add or remove steps in AI plans, TaskMind learns how you like to plan.[/dim]")
    ui.out("[dim]Stored only on this computer (~/.taskmind/config.json). Used for greetings, the language of "
           "answers, and reminders.[/dim]")


def cmd_week(args):
    db = open_db()
    w = insights.week(db)
    name = insights.profile(config.load())["name"]
    ui.heading(f"Your week{', ' + name if name else ''}")
    ui.plain(f"  ✅ {len(w['finished'])} task(s) finished   ·   {w['steps_done']} step(s) done   ·   "
             f"{w['planned']} planned   ·   {w['verified']} verified   ·   {w['added']} added")
    ui.bullets("Finished", [f"TM-{t['id']} {t['title']}" for t in w["finished"]])
    ui.bullets("Slipping (past due)", [f"TM-{t['id']} {t['title']} (due {t['due']})" for t in w["slipping"]])
    if w["busiest_day"]:
        ui.plain(f"\n  Your busiest day was {w['busiest_day']}. {w['open']} task(s) still open.")
    if not (w["finished"] or w["steps_done"] or w["added"]):
        ui.out("[dim]A quiet week. Add something with tm add \"...\" or just tm \"...\".[/dim]")


AUTOSTART = Path.home() / ".config" / "autostart" / "taskmind-greet.desktop"


def cmd_greet(args):
    if args.install or args.uninstall:
        if sys.platform != "linux":
            raise CLIError("The login greeting is available on Linux for now. On other systems, add "
                           "`tm greet --notify` to your login items.")
        if args.uninstall:
            AUTOSTART.unlink(missing_ok=True)
            ui.out("The login greeting is off.")
            return
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        env = f"env TASKMIND_HOME={shlex.quote(str(config.home()))} " if os.environ.get("TASKMIND_HOME") else ""
        AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=TaskMind greeting\n"
                             f"Exec=sh -c 'sleep 10; {env}{reminders.tm_executable()} greet --notify'\n"
                             "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n", encoding="utf-8")
        ui.out(f"[green]Done.[/green] Each time you log in, TaskMind greets you with your day "
               f"([dim]{ui.escape(str(AUTOSTART))}; undo with tm greet --uninstall[/dim]).")
        return
    db = open_db()
    text = insights.greeting(db, config.load(), reminders.schedule_enabled())
    if args.notify:
        title, _, body = text.partition("! ")
        reminders.notify(f"{title}!" if body else "TaskMind", body or text)
    ui.plain(text)
    if args.speak:
        make_speaker().say(text)


def maybe_greet(args):
    """The first time someone uses TaskMind each day, a one-line greeting with their day."""
    if (args.command in ("remind", "export", "backup", "greet", "tutorial", "chat") or getattr(args, "chat", False)
            or not sys.stdout.isatty()):
        return
    try:
        db = DB(config.db_path())
        today = date.today().isoformat()
        if db.get_meta("last_greeting") == today:
            return
        db.set_meta("last_greeting", today)
        ui.out(f"[dim]{ui.escape(insights.greeting(db, config.load(), reminders.schedule_enabled()))}[/dim]")
    except (OSError, sqlite3.Error, ValueError):
        pass


def cmd_report_bug(args):
    note = " ".join(args.what).strip()
    if not note and interactive():
        note = ui.ask("What happened, in a sentence or two? (Enter to skip)")
    text = diagnostics.report_text(note)
    out = Path(args.out).expanduser() if args.out else config.home() / f"bug-report-{datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text(text, encoding="utf-8")
    ui.out(f"[green]Saved your bug report:[/green] {ui.escape(str(out))}")
    ui.out("[dim]It has your TaskMind version, system, AI provider name and recent error messages; "
           "no API keys and no code. Have a look before sending it.[/dim]")
    link = diagnostics.issue_link(f"Bug: {note[:80] or 'TaskMind problem'}", text)
    if link:
        ui.out(f"Report it here (opens prefilled): {link}")
        if args.open:
            import webbrowser
            webbrowser.open(link)
    else:
        ui.out("Send this file to the person who gave you TaskMind (e-mail, chat), or attach it to an issue.")


def cmd_feedback(args):
    text = " ".join(args.text).strip()
    if not text and interactive():
        text = ui.ask("What would make TaskMind better for you?")
    if not text:
        raise CLIError('Write your feedback, e.g. tm feedback "the plans have too many steps"')
    path = config.home() / "feedback.txt"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M}\tTaskMind {__version__}\t{diagnostics.redact(text)}\n")
    ui.out("[green]Thank you![/green] Saved in " + ui.escape(str(path)))
    link = diagnostics.issue_link(f"Feedback: {text[:80]}", f"{text}\n\n_TaskMind {__version__}_")
    if link:
        ui.out(f"Share it with the team: {link}")
    else:
        ui.out("[dim]Share that file (and `tm wishlist`) with the person who gave you TaskMind.[/dim]")


def cmd_backup(args):
    db = DB(config.db_path())
    if args.restore:
        source = Path(args.restore).expanduser()
        if not source.exists():
            raise CLIError(f"No backup at {source}. See `tm backup --list`.")
        if not ui.confirm(f"Replace your current task list with {source.name}? (The current one is backed up first.)"):
            return
        db.backup_to(backups_dir() / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
        backup = DB(source)
        backup.backup_to(config.db_path())
        backup.close()
        ui.out(f"[green]Restored from {ui.escape(source.name)}.[/green]")
        return
    if args.list:
        files = sorted(backups_dir().glob("*.db"), reverse=True)
        if not files:
            ui.out("No backups yet. TaskMind makes one automatically each day you use it, or run `tm backup`.")
        for f in files:
            ui.plain(f"  {f}  ({f.stat().st_size // 1024} KB)")
        return
    target = Path(args.out).expanduser() if args.out else backups_dir() / f"taskmind-{datetime.now():%Y%m%d-%H%M%S}.db"
    db.backup_to(target)
    ui.out(f"[green]Backed up to {ui.escape(str(target))}[/green]\n"
           f"[dim]Restore with: tm backup --restore {ui.escape(str(target))}[/dim]")


def cmd_export(args):
    data = open_db().export()
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).expanduser().write_text(text, encoding="utf-8")
        ui.out(f"Exported {len(data['tasks'])} task(s) to {ui.escape(args.out)}")
    else:
        print(text)


def wishlist_path():
    return config.home() / "wishlist.txt"


def add_to_wishlist(request, reason):
    with open(wishlist_path(), "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M}\t{request}\t{reason}\n")


def cmd_wishlist(args):
    path = wishlist_path()
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        ui.out("Your wishlist is empty. When you ask `tm do` for something TaskMind can't do yet, "
               "it's saved here.")
        return
    ui.heading("Things you asked for that TaskMind can't do yet")
    for line in path.read_text(encoding="utf-8").splitlines():
        when, _, rest = line.partition("\t")
        request, _, reason = rest.partition("\t")
        ui.plain(f"  {when}  {request}" + (f"\n      ({reason})" if reason else ""))
    ui.out(f"[dim]Saved in {ui.escape(str(path))}[/dim]")
