"""Terminal output. Uses `rich` when installed, plain text otherwise."""

import contextlib
import re
import sys

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.markup import escape
    from rich.table import Table

    _console = Console(highlight=False)
except ImportError:  # rich is optional
    _console = None

    def escape(text):
        return text


# Simple mode (for non-developers) shows "Task 12 step 3" instead of "TM-12.3".
SIMPLE = False


def humanize(text):
    if not SIMPLE or not isinstance(text, str):
        return text
    text = re.sub(r"\bTM-(\d+)\.(\d+)\b", r"Task \1 step \2", text)
    return re.sub(r"\bTM-(\d+)\b", r"Task \1", text)


def out(text="", style=None):
    """Print text that may contain rich markup like [bold]...[/bold]."""
    text = humanize(text)
    if _console:
        _console.print(text, style=style)
    else:
        print(re.sub(r"\[/?[a-z ]+\]", "", text))


def plain(text=""):
    """Print user or model text verbatim (no markup interpretation)."""
    text = humanize(text)
    if _console:
        _console.print(text, markup=False)
    else:
        print(text)


def heading(text):
    text = humanize(text)
    out(f"\n[bold cyan]{escape(text)}[/bold cyan]" if _console else f"\n== {text} ==")


def error(text):
    if _console:
        Console(stderr=True).print(f"[bold red]Error:[/bold red] {escape(text)}")
    else:
        print(f"Error: {text}", file=sys.stderr)


def markdown(text):
    if _console:
        _console.print(Markdown(text))
    else:
        print(text)


def bullets(title, items):
    if not items:
        return
    heading(title)
    for item in items:
        plain(f"  • {item}")


def table(columns, rows, title=None):
    rows = [[humanize(str(c)) for c in row] for row in rows]
    if _console:
        t = Table(title=title, show_lines=False, header_style="bold")
        for col in columns:
            t.add_column(col, overflow="fold")
        for row in rows:
            t.add_row(*(escape(str(c)) for c in row))
        _console.print(t)
        return
    if title:
        print(title)
    widths = [max(len(str(c)) for c in [col, *[r[i] for r in rows]]) for i, col in enumerate(columns)]
    print("  ".join(str(c).ljust(w) for c, w in zip(columns, widths)))
    for row in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))


def ask(prompt):
    try:
        return input(f"{prompt} ").strip()
    except EOFError:
        return ""


def secret(prompt):
    """Ask for something sensitive (an API key) without echoing it."""
    import getpass
    try:
        return getpass.getpass(f"{prompt} ").strip()
    except EOFError:
        return ""


YES = {"y", "yes", "yeah", "yep", "ok", "okay", "sure", "haan", "han", "ha", "haa", "ji"}
NO = {"n", "no", "nope", "nahi", "nahin", "na", "mat"}


def confirm(prompt, default=False):
    """Yes/no question. Enter takes the default; anything unclear is asked again, never guessed."""
    hint = "[Y/n]" if default else "[y/N]"
    for _ in range(3):
        answer = ask(f"{prompt} {hint}").lower().strip(" .!")
        if not answer:
            return default
        if answer in YES:
            return True
        if answer in NO:
            return False
        out("[yellow]Please answer y (yes) or n (no), or press Enter for the default.[/yellow]")
    return default


@contextlib.contextmanager
def working(message):
    if _console and _console.is_terminal:
        with _console.status(message):
            yield
    else:
        out(message)
        yield
