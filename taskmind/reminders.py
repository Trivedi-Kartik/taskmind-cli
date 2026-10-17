"""Reminders: desktop notifications (optionally spoken) when a task needs attention.

Three kinds, each sent once:
  remind   - at the time you set with --remind
  due      - on the due date, at your daily reminder time (default 09:00)
  overdue  - once a day while a task is past its due date

`tm remind check` sends whatever is due now. `tm remind on` schedules that check
every 5 minutes with cron (Linux/macOS), so it works with the terminal closed.
"""

import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_TIME = "09:00"
CRON_TAG = "# taskmind-reminders"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SESSION_VARS = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def parse_time_of_day(text):
    """'9', '9am', '9:30 pm', '18:00' -> (hour, minute), or None."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text.strip().lower())
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def parse_when(text, now=None) -> str:
    """Turn '+30m', '+2h', '18:00', '9am', 'tomorrow 9am', 'friday 10:30', '2026-10-20 14:00'
    or '2026-10-20' into 'YYYY-MM-DD HH:MM' (local time)."""
    now = now or datetime.now()
    t = " ".join(text.strip().lower().split())
    m = re.fullmatch(r"\+(\d+)\s*([mhd])", t)
    if m:
        unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        return _fmt(now + timedelta(**{unit: int(m.group(1))}))
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ t](.+))?", t)
    if m:
        day = date.fromisoformat(m.group(1))
        hm = parse_time_of_day(m.group(2)) if m.group(2) else parse_time_of_day(DEFAULT_TIME)
        if hm is None:
            raise ValueError(f"Can't read the time in '{text}'.")
        return _fmt(datetime(day.year, day.month, day.day, *hm))
    words = t.split(" ", 1)
    day_word, rest = (words[0], words[1] if len(words) > 1 else "") if words[0] in (
        ["today", "tonight", "tomorrow"] + WEEKDAYS) else (None, t)
    hm = parse_time_of_day(rest) if rest else None
    if rest and hm is None:
        raise ValueError(f"Can't read '{text}'. Try +30m, 18:00, tomorrow 9am or 2026-10-20 14:00.")
    if day_word is None:  # just a time: today, or tomorrow if already past
        target = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        return _fmt(target if target > now else target + timedelta(days=1))
    if day_word in ("today", "tonight"):
        day = now.date()
        if day_word == "tonight":
            hm = hm or (20, 0)
            if hm[0] < 12 and not re.search(r"am\b", rest):  # "tonight 9" means 21:00
                hm = (hm[0] + 12, hm[1])
        hm = hm or parse_time_of_day(DEFAULT_TIME)
    elif day_word == "tomorrow":
        day = now.date() + timedelta(days=1)
    else:
        ahead = (WEEKDAYS.index(day_word) - now.weekday()) % 7
        hm = hm or parse_time_of_day(DEFAULT_TIME)
        # The same weekday means today if that time is still ahead, otherwise next week.
        if ahead == 0 and (hm[0], hm[1]) <= (now.hour, now.minute):
            ahead = 7
        day = now.date() + timedelta(days=ahead)
    hm = hm or parse_time_of_day(DEFAULT_TIME)
    return _fmt(datetime(day.year, day.month, day.day, *hm))


def pending(db, cfg, now=None) -> list:
    """Reminders that should fire now and haven't been sent: (task, kind, key, message)."""
    now = now or datetime.now()
    today = now.date().isoformat()
    # A hand-edited, unreadable daily time falls back to the default instead of breaking every check.
    daily = parse_time_of_day((cfg.get("reminders") or {}).get("time") or DEFAULT_TIME) \
        or parse_time_of_day(DEFAULT_TIME)
    after_daily_time = (now.hour, now.minute) >= daily
    items = []
    for task in db.list_tasks():
        try:
            if task.get("remind_at") and task["remind_at"] <= _fmt(now):
                items.append((task, "remind", task["remind_at"], f"Reminder: {task['title']}"))
            due = task.get("due")
            if due and after_daily_time:
                if due == today:
                    items.append((task, "due", due, f"Due today: {task['title']}"))
                elif due < today:
                    days = (now.date() - date.fromisoformat(due)).days
                    items.append((task, "overdue", today,
                                  f"Overdue by {days} day{'s' if days != 1 else ''}: {task['title']}"))
        except ValueError:
            continue  # one task with an impossible date must not stop everyone's reminders
    return [i for i in items if not db.reminder_sent(i[0]["id"], i[1], i[2])]


def next_step(db, task) -> str:
    step = next((s for s in db.subtasks(task["id"]) if not s["done"]), None)
    return f"Next: {step['title']}" if step else ""


def notify(title, body) -> bool:
    """Show a desktop notification. Returns False when no notifier is available."""
    system = platform.system()
    try:
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "TaskMind", title, body], check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if system == "Darwin" and shutil.which("osascript"):
            script = f"display notification {_applescript(body)} with title {_applescript(title)}"
            subprocess.run(["osascript", "-e", script], check=True, timeout=10)
            return True
        if system == "Windows" and shutil.which("powershell"):
            script = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
                      "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
                      "$n.ShowBalloonTip(10000, $env:TM_TITLE, $env:TM_BODY, 'Info'); Start-Sleep 6; $n.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=30,
                           env={**os.environ, "TM_TITLE": title, "TM_BODY": body})
            return True
    except (subprocess.SubprocessError, OSError):
        return False
    return False


def _applescript(text) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check(db, cfg, speak=None, now=None) -> list:
    """Send pending reminders. Returns the messages sent."""
    sent = []
    for task, kind, key, message in pending(db, cfg, now):
        body = next_step(db, task)
        shown = notify(message, body)
        # Mark it sent before speaking: a voice failure must never repeat the reminder.
        db.mark_reminder_sent(task["id"], kind, key)
        if speak:
            try:
                speak(f"{message}. {body}")
            except Exception:  # noqa: BLE001 - speaking is a nice-to-have
                pass
        db.log(task["id"], "reminder", message + ("" if shown else " (no desktop notifier found)"))
        sent.append(f"{message}" + (f" ({body})" if body else ""))
    return sent


# Scheduling with cron

def cron_line(tm_command, speak=False) -> str:
    """A crontab line that checks every 5 minutes, carrying the desktop session so
    notifications reach the screen (cron jobs don't inherit it)."""
    env = [f"{v}={shlex.quote(os.environ[v])}" for v in SESSION_VARS if os.environ.get(v)]
    if os.environ.get("PATH"):  # cron's PATH is minimal (no Homebrew, no ~/.local/bin)
        env.append(f"PATH={shlex.quote(os.environ['PATH'])}")
    if os.environ.get("TASKMIND_HOME"):
        env.append(f"TASKMIND_HOME={shlex.quote(os.environ['TASKMIND_HOME'])}")
    # API keys are read from TaskMind's config file, never copied into the crontab.
    cmd = " ".join(env + [tm_command, "remind", "check", "--quiet"] + (["--speak"] if speak else []))
    return f"*/5 * * * * {cmd.replace('%', chr(92) + '%')} {CRON_TAG}"


def _crontab() -> str:
    if not shutil.which("crontab"):
        raise RuntimeError("Scheduling needs cron (Linux/macOS). Use `tm remind watch` in a terminal instead.")
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _write_crontab(text):
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def schedule_enabled() -> bool:
    try:
        return CRON_TAG in _crontab()
    except RuntimeError:
        return False


def enable(tm_command, speak=False):
    lines = [l for l in _crontab().splitlines() if CRON_TAG not in l]
    lines.append(cron_line(tm_command, speak))
    _write_crontab("\n".join(lines) + "\n")


def disable():
    current = _crontab()
    if CRON_TAG not in current:
        return
    rest = [l for l in current.splitlines() if CRON_TAG not in l]
    if any(l.strip() for l in rest):
        _write_crontab("\n".join(rest) + "\n")
    else:
        subprocess.run(["crontab", "-r"], check=False, capture_output=True)  # leave no empty crontab


def tm_executable() -> str:
    """Shell command cron uses to call TaskMind: the `tm` launcher next to this package,
    `tm` on PATH, or the current Python running the package."""
    here = Path(__file__).resolve()
    pyz = next((p for p in here.parents if p.suffix == ".pyz" and p.is_file()), None)
    if pyz:  # running from the single-file download
        return f"{shlex.quote(sys.executable)} {shlex.quote(str(pyz))}"
    launcher = here.parent.parent / "tm"
    if launcher.exists():
        return shlex.quote(str(launcher))
    found = shutil.which("tm")
    return shlex.quote(found) if found else f"{shlex.quote(sys.executable)} -m taskmind"
