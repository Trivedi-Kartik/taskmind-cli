"""Understanding what people type: task and step numbers, dates, typos and friendly usage errors."""

import argparse
import difflib
import re
from datetime import date, timedelta

from taskmind import reminders
from taskmind.core import CLIError


def parse_ref(text):
    """'12' -> (12, None); '12.3' or 'TM-12.3' -> (12, 3)."""
    text = text.upper().removeprefix("TM-")
    task_id, sep, pos = text.partition(".")
    try:
        if sep and not pos:
            raise ValueError("missing step number")
        return int(task_id), int(pos) if pos else None
    except ValueError as exc:
        raise CLIError(f"Not a task reference: {text} (use 12 or 12.3)") from exc


def parse_due(text):
    if not text:
        return None
    t = text.lower()
    today = date.today()
    if t == "today":
        return today.isoformat()
    if t == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if t.startswith("+") and t.endswith("d") and t[1:-1].isdigit():
        return (today + timedelta(days=int(t[1:-1]))).isoformat()
    if t in reminders.WEEKDAYS:  # that day this week: today if it's today, otherwise the next one
        ahead = (reminders.WEEKDAYS.index(t) - today.weekday()) % 7
        return (today + timedelta(days=ahead)).isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise CLIError("Due date must be YYYY-MM-DD, today, tomorrow, a weekday (friday) or +Nd") from exc


class UsageError(Exception):
    def __init__(self, prog, message):
        super().__init__(message)
        self.prog, self.message = prog, message


class FriendlyParser(argparse.ArgumentParser):
    """Turns argparse's terse errors into plain-language ones (see friendly_usage_error)."""

    def error(self, message):
        raise UsageError(self.prog, message)


QUESTION_WORDS = {"how", "what", "why", "where", "which", "who", "when", "can", "could", "should", "is",
                  "are", "does", "do", "explain", "show", "i", "my", "kaise", "kya", "kyu", "kyun"}


def closest_command(word, commands):
    """The command a typo was meant to be: swapped letters ("og" -> "go") or a close spelling ("lst")."""
    word = word.lower()
    swapped = [c for c in commands if len(c) == len(word) and sorted(c) == sorted(word)]
    if swapped:
        return swapped[0]
    close = difflib.get_close_matches(word, commands, n=1, cutoff=0.6)
    return close[0] if close else None


REF_RE = re.compile(r"^(TM-)?\d+(\.\d+)?$", re.I)


SENTENCE_WORDS = {"me", "my", "the", "to", "about", "for", "please", "a", "an", "all", "and", "in", "on", "at",
                  "of", "with", "that", "this", "it", "today", "tomorrow", "mujhe", "mera", "meri", "kal",
                  "aaj", "ko", "ka", "ki", "ke", "hai", "karo", "do"}


def looks_like_a_sentence(argv) -> bool:
    words = [w.lower() for w in argv if not w.startswith("-")]
    return len(words) >= 4 or (len(words) >= 2 and any(w in SENTENCE_WORDS for w in words[1:]))


def friendly_usage_error(err, commands, argv=()) -> str:
    command = err.prog.split(" ", 1)[1] if " " in err.prog else (argv[0] if argv and argv[0] in commands else "")
    msg = err.message
    m = re.search(r"argument <command>: invalid choice: '([^']*)'", msg)
    if m:
        close = closest_command(m.group(1), commands)
        hint = f" Did you mean `tm {close}`?" if close else ""
        return f"`{m.group(1)}` isn't a TaskMind command.{hint} Type `tm` for the main commands."
    m = re.search(r"the following arguments are required: (.+)", msg)
    if m:
        missing = m.group(1)
        if "ref" in missing:
            return (f"`tm {command}` needs a task or step number, e.g. `tm {command} 1` or "
                    f"`tm {command} 1.2`. See your tasks with `tm list`.")
        if "id" in missing:
            return f"`tm {command}` needs a task number, e.g. `tm {command} 1`. See your tasks with `tm list`."
        if "action" in missing:
            return f"`tm {command}` needs to know what to do. See the options with `tm {command} --help`."
        if "title" in missing:
            return f'`tm {command}` needs a title in quotes, e.g. tm {command} "Fix the login bug".'
        return f"`tm {command}` is missing: {missing}. See `tm {command} --help`."
    m = re.search(r"invalid int value: '([^']*)'", msg)
    if m:
        return f"'{m.group(1)}' isn't a task number. Use a number like 1 (see `tm list`)."
    m = re.search(r"invalid choice: '([^']*)' \(choose from (.+)\)", msg)
    if m:
        options = m.group(2).replace("'", "")
        close = difflib.get_close_matches(m.group(1), [o.strip() for o in options.split(",")], n=1, cutoff=0.5)
        hint = f" Did you mean `{close[0]}`?" if close else ""
        return f"'{m.group(1)}' isn't an option here.{hint} Choose from: {options}."
    m = re.search(r"unrecognized arguments: (.+)", msg)
    if m:
        return (f"`tm {command}` doesn't understand: {m.group(1)}. Put text with spaces in quotes, "
                f"and see `tm {command} --help`.")
    return f"{msg}. See `tm {command} --help`." if command else f"{msg}. Type `tm` for the main commands."
