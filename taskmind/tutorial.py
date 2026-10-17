"""`tm tutorial`: a guided first task that explains every command before and after it runs.

It uses a separate practice task list and a tiny practice project, so the user's
real tasks and code are never touched.
"""

import os
import shlex
import shutil
import subprocess
from contextlib import contextmanager

from taskmind import config, ui

OVERVIEW = """[bold]TaskMind: a to-do list that thinks.[/bold] A task goes through four phases:

  [bold]1. WRITE IT DOWN[/bold]  →  [bold]2. THINK IT THROUGH[/bold]  →  [bold]3. DO IT[/bold]  →  [bold]4. CHECK IT[/bold]
     tm add             tm go                  tm prompt     tm verify
                                               tm done

  tm add "..."     write a task down (it gets a number: 1, 2, 3 ...)
  tm list          show your to-do list
  tm show 1        everything about task 1
  tm go 1          AI asks questions, reads your code, plans steps 1.1, 1.2
  tm prompt 1.1    instructions for step 1.1, to paste into Claude Code
  tm done 1.1      tick off step 1.1 (tm sync does it from git commits)
  tm verify 1      AI checks the finished code against the plan
  tm today         what to work on today

  Or just say it:  tm "remind me about task 1 tomorrow at 9"
                   (I show the commands I'll run and ask first)

  [bold]New here?[/bold] Run [bold cyan]tm tutorial[/bold cyan] for a guided 10-minute first task.
  All commands: tm --help      Settings: tm setup"""

PRACTICE_APP = '''import sqlite3

conn = sqlite3.connect("notes.db")


def notes_for(user_id):
    return conn.execute("SELECT id, title, body FROM notes WHERE user_id = ?", (user_id,)).fetchall()


def handle_notes(request):
    if not request.user_id:
        return 401, {"error": "login required"}
    return 200, [{"id": n[0], "title": n[1], "body": n[2]} for n in notes_for(request.user_id)]
'''

PRACTICE_CHANGE = '''

def export_csv(request):
    """Download the logged-in user's notes as a CSV file."""
    import csv
    import io
    if not request.user_id:
        return 401, {"error": "login required"}
    out = io.StringIO()
    csv.writer(out).writerows([("title", "body")] + [(n[1], n[2]) for n in notes_for(request.user_id)])
    return 200, out.getvalue()
'''

TASK = "Let users download their notes as a CSV file"


def lesson(n, total, title, before):
    ui.out(f"\n[bold magenta]━━ Lesson {n} of {total} · {title} ━━[/bold magenta]")
    ui.out(before)


def pause(prompt="Press Enter to continue"):
    ui.ask(f"\n[{prompt}]")


def run_command(main, expected):
    """Ask the user to type the command (Enter runs it for them), then run it."""
    shown = "tm " + " ".join(shlex.quote(a) for a in expected)
    ui.out(f"\n[bold]Type this command[/bold] (or just press Enter and I'll type it for you):\n"
           f"  [bold cyan]{ui.escape(shown)}[/bold cyan]")
    while True:
        typed = ui.ask(">")
        if not typed:
            break
        try:
            words = shlex.split(typed)
        except ValueError:
            words = []
        if words[:1] == ["tm"]:
            words = words[1:]
        if words == expected:
            break
        ui.out(f"[yellow]Not quite. It should be exactly:[/yellow] {ui.escape(shown)} [dim](or press Enter)[/dim]")
    ui.out(f"[dim]$ {ui.escape(shown)}[/dim]")
    return main(expected)


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=TaskMind tutorial",
                    "-c", "user.email=tutorial@taskmind.local", *args], check=True, capture_output=True)


@contextmanager
def practice_space():
    """A separate task list and practice project, with the user's AI settings copied in."""
    real_home = config.home()
    space = real_home / "tutorial"
    if space.exists():
        shutil.rmtree(space)
    data, project = space / "data", space / "notes-app"
    data.mkdir(parents=True)
    project.mkdir()
    if config.config_path().exists():
        shutil.copy(config.config_path(), data / "config.json")
        (data / "config.json").chmod(0o600)
    (project / "app.py").write_text(PRACTICE_APP)
    (project / ".gitignore").write_text("notes.db\n")
    git(project, "init", "-q")
    git(project, "add", ".")
    git(project, "commit", "-qm", "Notes app")
    old_home, old_cwd = os.environ.get("TASKMIND_HOME"), os.getcwd()
    os.environ["TASKMIND_HOME"] = str(data)
    os.chdir(project)
    try:
        yield space, project
    finally:
        os.chdir(old_cwd)
        if old_home is None:
            os.environ.pop("TASKMIND_HOME", None)
        else:
            os.environ["TASKMIND_HOME"] = old_home


def trap_feedback() -> str:
    """Tell the truth about whether the analysis caught the login trap."""
    from taskmind.db import DB  # here, so it opens the practice list
    analysis = DB(config.db_path()).get_task(1).get("analysis") or {}
    auth = next((s for s in analysis.get("security") or [] if s["check"].startswith("Authentication")), None)
    if auth and auth["status"] == "risk":
        return ("[green]It caught the trap:[/green] [bold]Authentication[/bold] is marked ⚠️ risk, so "
                "the plan makes sure the download checks the login.")
    return ("[yellow]It missed the trap this time:[/yellow] the download must also check the login, but "
            "Authentication isn't marked ⚠️. AI judgment varies, which is why `tm verify` (lesson 7) and "
            "your own read of the plan matter.")


def run(main):
    """The tutorial itself. `main` is cli.main, so every command runs exactly as typed."""
    total = 8
    ui.out("\n[bold]Welcome to TaskMind![/bold] In about 10 minutes you'll take one task from idea to "
           "checked code, and learn every main command along the way.")
    ui.out(OVERVIEW.split("\n\n  tm add")[0])
    ui.out("\n[dim]I'll create a [bold]practice project[/bold] (a tiny pretend notes app) and a separate "
           "practice to-do list, so your real tasks and code are never touched. A few AI calls are used.[/dim]")
    if not ui.confirm("Ready to start?", default=True):
        return 0
    with practice_space() as (space, project):
        ui.out(f"\n[dim]Practice project: {ui.escape(str(project))}[/dim]")
        ui.out("It's one file, [bold]app.py[/bold]: the part of a notes app (like Google Keep) that shows you "
               "your notes, but [bold]only if you're logged in[/bold]. You'll never run it; it's there for "
               "TaskMind to read.")
        pause()

        lesson(1, total, "Write it down: tm add",
               "Imagine your manager asks for a new feature: a button to download your notes as a "
               "spreadsheet (CSV) file. First, write the task down. Your own rough words are fine; no AI "
               "is used yet.")
        run_command(main, ["add", TASK])
        ui.out("\n[green]What happened:[/green] the task was saved as [bold]task 1[/bold] (shown as TM-1). "
               "Because you were inside the practice project, TaskMind linked the task to that code "
               "(\"repo:\"), so later it can read it.")
        pause()

        lesson(2, total, "See your list: tm list", "Let's look at your to-do list.")
        run_command(main, ["list"])
        ui.out("\n[green]What happened:[/green] one task, status [bold]inbox[/bold]: written down, not "
               "thought through yet. \"Done\" shows progress once it has steps (like 2/8).")
        pause()

        lesson(3, total, "Think it through: tm go",
               "Now the AI takes over, like a senior colleague, in three stages:\n"
               "  • it asks what's unclear (answer what you know, or press Enter to skip),\n"
               "  • it reads the practice code to find what the task touches and what could go wrong,\n"
               "  • it splits the work into small steps: 1.1, 1.2, 1.3 ...\n"
               "[dim]Tip: there's a trap in this task. The download must also check that you're logged in. "
               "Watch whether TaskMind notices.[/dim]")
        pause("Press Enter, then type the command")
        code = run_command(main, ["go", "1"])
        if code:
            ui.out("\n[yellow]The AI step didn't finish (see the message above). Fix it (often `tm setup`), "
                   "then run `tm tutorial` again.[/yellow]")
            return code
        ui.out("\n[green]What happened:[/green] your rough task became a clear one with \"done when\" "
               "rules, the AI read app.py and listed the risks, then made the steps. [bold]1.1[/bold] means "
               "task 1, step 1.")
        ui.out(trap_feedback())
        pause()

        lesson(4, total, "Do it: tm prompt",
               "TaskMind doesn't write code itself; Claude Code, Cursor or you do. `tm prompt` turns a "
               "step into complete instructions for them, including the risks to handle.")
        run_command(main, ["prompt", "1.1", "--print"])
        ui.out("\n[green]What happened:[/green] those are the instructions you'd paste into Claude Code. "
               "(Without --print they're copied to your clipboard.) They ask for \"TM-1.1\" in the commit "
               "message, which is how TaskMind follows your progress.")
        pause()

        lesson(5, total, "Tick steps off automatically: tm sync",
               "Let's pretend Claude Code wrote the code for step 1. I'll add a small CSV download "
               "function to the practice project and save it with git, with \"TM-1.1\" in the message.")
        with open(project / "app.py", "a") as fh:
            fh.write(PRACTICE_CHANGE)
        git(project, "add", ".")
        git(project, "commit", "-qm", "Add CSV download TM-1.1")
        ui.out("[dim]$ git commit -m \"Add CSV download TM-1.1\"[/dim]")
        run_command(main, ["sync"])
        ui.out("\n[green]What happened:[/green] TaskMind saw \"TM-1.1\" in the commit and ticked step 1 "
               "off, and task 1 is now in progress. No AI needed.")
        pause()

        lesson(6, total, "Tick a step off by hand: tm done",
               "Not everything is a commit (a meeting, a review). Tick a step off yourself:")
        run_command(main, ["done", "1.2"])
        ui.out("\n[green]What happened:[/green] step 2 is ticked off too. Made a mistake? "
               "`tm done 1.2 --undo` reopens it.")
        pause()

        lesson(7, total, "Check it: tm verify",
               "Before calling a task finished, the AI compares the code that was written against the "
               "plan: every \"done when\" rule, edge case, security risk and test.")
        run_command(main, ["verify", "1"])
        ui.out("\n[green]What happened:[/green] each item got ✅ done, 🟡 partial, ❌ missing or ❔ unclear, "
               "with evidence. Only part of the plan was built, so seeing ❌ items is expected; in real use "
               "you'd fix them and run `tm verify` again.")
        pause()

        lesson(8, total, "Every morning: tm today", "Finally, the command to start each day with:")
        run_command(main, ["today"])
        ui.out("\n[green]What happened:[/green] what's overdue, due, in progress (with the next step) and "
               "ready to start. No AI needed.")

        ui.out("\n[bold green]🎉 You've done the whole flow: add → go → prompt → sync/done → verify → today.[/bold green]")
        ui.out("\n[bold]Use it for real:[/bold] go to one of your projects and add a real task:\n"
               "  cd ~/path/to/your-project\n"
               "  tm add \"something you need to do\"\n"
               "  tm go 1\n"
               "\n[bold]Extras when you're ready:[/bold] reminders (tm remind), voice (tm add --voice), "
               "calendar (tm calendar export), everyday tasks (tm add \"...\" --no-repo).\n"
               "Type [bold]tm[/bold] any time for the short overview.")
        keep = ui.confirm("\nKeep the practice project and list to look at later?", default=False)
    if keep:
        ui.out(f"[dim]Kept in {ui.escape(str(space))}. View it with: "
               f"TASKMIND_HOME={ui.escape(str(space / 'data'))} tm list[/dim]")
    else:
        shutil.rmtree(space, ignore_errors=True)
        ui.out("[dim]Practice files removed. Your real task list was never changed.[/dim]")
    return 0
