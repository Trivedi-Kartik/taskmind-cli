"""Calendar: export tasks as an .ics file and build 'add to Google Calendar' links.

Both work with any calendar app and need no accounts or API keys. Due dates become
all-day events; reminders become 15-minute events with an alert. Times are
"floating" (no time zone), so calendars show them in your own local time.
"""

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

PRODID = "-//TaskMind//Tasks//EN"
REMINDER_MINUTES = 15
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _updated(task):
    try:
        return datetime.fromisoformat(task["updated_at"]).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _escape(text) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line) -> str:
    """iCalendar lines are at most 75 octets; continuation lines start with a space."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += encoded
    out.append(current.decode())
    return "\r\n ".join(out)


def _details(task, steps) -> str:
    lines = []
    if task.get("description"):
        lines.append(task["description"])
    open_steps = [s for s in steps if not s["done"]]
    if open_steps:
        lines.append("Steps:")
        lines += [f"- {s['title']}" for s in open_steps[:10]]
    lines.append(f"TaskMind task {task['id']}: tm show {task['id']}")
    return "\n".join(lines)


def events(task, steps) -> list:
    """(kind, start, end, all_day) for a task's due date and reminder."""
    found = []
    if task.get("due"):
        day = date.fromisoformat(task["due"])
        found.append(("due", day, day + timedelta(days=1), True))
    if task.get("remind_at"):
        start = datetime.strptime(task["remind_at"], "%Y-%m-%d %H:%M")
        found.append(("remind", start, start + timedelta(minutes=REMINDER_MINUTES), False))
    return found


def to_ics(items) -> str:
    """items: list of (task, steps). Returns the .ics file text."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:TaskMind"]
    for task, steps in items:
        for kind, start, end, all_day in events(task, steps):
            prefix = "Due: " if kind == "due" else ""
            lines += ["BEGIN:VEVENT", f"UID:taskmind-{task['id']}-{kind}@taskmind", f"DTSTAMP:{stamp}"]
            updated = _updated(task)
            if updated:  # lets calendars tell a changed event from an old copy
                lines += [f"LAST-MODIFIED:{updated:%Y%m%dT%H%M%SZ}",
                          f"SEQUENCE:{max(0, int((updated - EPOCH).total_seconds() // 60))}"]
            if all_day:
                lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}",
                          "TRANSP:TRANSPARENT"]
            else:
                lines += [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}"]
            lines += [f"SUMMARY:{_escape(prefix + task['title'])}",
                      f"DESCRIPTION:{_escape(_details(task, steps))}"]
            if task.get("status") == "done":
                lines.append("STATUS:CANCELLED")
            else:
                trigger = "PT9H" if all_day else "PT0M"  # 9:00 on the due day, or at the reminder time
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(task['title'])}",
                          f"TRIGGER;RELATED=START:{trigger}", "END:VALARM"]
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def google_links(task, steps) -> list:
    """(label, url) per event; opens Google Calendar's 'new event' page, prefilled."""
    links = []
    for kind, start, end, all_day in events(task, steps):
        fmt = "%Y%m%d" if all_day else "%Y%m%dT%H%M%S"
        params = {"action": "TEMPLATE", "text": ("Due: " if kind == "due" else "") + task["title"],
                  "dates": f"{start:{fmt}}/{end:{fmt}}", "details": _details(task, steps)}
        links.append(("due date" if kind == "due" else "reminder",
                      "https://calendar.google.com/calendar/render?" + urlencode(params)))
    return links
