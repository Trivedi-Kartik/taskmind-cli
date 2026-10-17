"""Live end-to-end smoke test: runs every TaskMind command the way a new user would.

Uses your real AI provider settings (copied from ~/.taskmind/config.json) but a
temporary data folder and a temporary git project, so your own tasks are untouched.
Costs a few AI calls (roughly 60-90k tokens on Groq's free tier).

    python3 tests/live_smoke.py            # everything
    python3 tests/live_smoke.py --no-ai    # only commands that don't call an AI

It never turns on cron reminders, records audio or plays sound. `tm remind check`
may show one desktop notification.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TM = str(ROOT / "tm")  # override with --tm to test an installed copy

APP = '''import sqlite3

conn = sqlite3.connect("notes.db")


def notes_for(user_id):
    return conn.execute("SELECT id, title, body FROM notes WHERE user_id = ?", (user_id,)).fetchall()


def handle_notes(request):
    user_id = request.user_id
    if not user_id:
        return 401, {"error": "login required"}
    return 200, [{"id": n[0], "title": n[1], "body": n[2]} for n in notes_for(user_id)]
'''


class Runner:
    def __init__(self, home, cwd):
        self.env = {**os.environ, "TASKMIND_HOME": str(home)}
        self.cwd = cwd
        self.results = []

    def run(self, name, args, expect_code=0, expect=(), reject=(), cwd=None, stdin="", shell=False):
        start = time.time()
        cmd = args if shell else [TM, *args]
        proc = subprocess.run(cmd, cwd=cwd or self.cwd, env=self.env, input=stdin, text=True,
                              capture_output=True, timeout=900, shell=shell)
        out = proc.stdout + proc.stderr
        problems = []
        codes = expect_code if isinstance(expect_code, (tuple, list)) else (expect_code,)
        if proc.returncode not in codes:
            problems.append(f"exit code {proc.returncode}, expected {expect_code}")
        problems += [f"missing '{e}'" for e in expect if e not in out]
        problems += [f"unexpected '{r}'" for r in reject if r in out]
        if "Traceback" in out:
            problems.append("Python traceback in output")
        ok = not problems
        self.results.append((name, ok, time.time() - start, problems, out))
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {name}  ({time.time() - start:.1f}s)" + ("" if ok else "\n      " + "; ".join(problems)))
        if not ok:
            print("      " + out.strip().replace("\n", "\n      ")[-1500:])
            if os.environ.get("GITHUB_ACTIONS"):  # visible on GitHub without logging in
                detail = ("; ".join(problems) + "\n" + out.strip()[-1500:]).replace("%", "%25").replace("\n", "%0A")
                print(f"::error title=smoke test: {name}::{detail}")
        return out


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=smoke", "-c", "user.email=smoke@test", *args],
                   check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-ai", action="store_true", help="Skip commands that call an AI provider")
    parser.add_argument("--tm", help="The tm command to test (e.g. an installed one); default: this checkout")
    args = parser.parse_args()
    global TM
    if args.tm:
        TM = shutil.which(args.tm) or args.tm
    work = Path(tempfile.mkdtemp(prefix="taskmind-smoke-"))
    home, repo = work / "home", work / "notesapp"
    home.mkdir()
    repo.mkdir()
    user_config = Path(os.environ.get("TASKMIND_HOME") or Path.home() / ".taskmind") / "config.json"
    if user_config.exists():
        shutil.copy(user_config, home / "config.json")
    elif not args.no_ai:
        sys.exit("No ~/.taskmind/config.json found. Run `tm setup` first, or use --no-ai.")
    (repo / "app.py").write_text(APP)
    (repo / ".gitignore").write_text("notes.db\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Notes app")
    r = Runner(home, repo)
    ai = not args.no_ai
    print(f"Working in {work}\n")

    # Basics
    r.run("version", ["--version"], expect=["taskmind"])
    r.run("help lists every command", ["--help"], expect=["setup", "verify", "remind", "calendar", "voice"])
    r.run("config shows settings", ["config"], expect=["provider", "data folder"])
    if ai:
        r.run("doctor", ["doctor"], expect=["OK"])
        r.run("doctor --all", ["doctor", "--all"], expect=["OK"])
        r.run("models", ["models"], expect=["models"])

    # Developer flow
    r.run("add links the repo", ["add", "Let users export their notes as CSV", "-p", "high", "--due", "+3d"],
          expect=["TM-1", "repo:"])
    r.run("list", ["list"], expect=["TM-1", "inbox"])
    r.run("show", ["show", "1"], expect=["Let users export"])
    r.run("bad id fails cleanly", ["show", "99"], expect_code=1, expect=["No task TM-99"])
    r.run("list piped into head", f"{shlex.quote(TM)} list | head -1", shell=True, expect_code=0)
    if ai:
        r.run("go (clarify, analyze, plan)", ["go", "1", "--no-questions"],
              expect=["The task, written down clearly", "Security check", "TM-1.1", "is planned"],
              reject=["tool calling failed"])
        r.run("show has analysis and AI log", ["show", "1"], expect=["Files this task touches", "AI used"])
        r.run("prompt", ["prompt", "1.1", "--print"], expect=["DO THIS ONE NOW", "Security requirements"])
        r.run("prompt to file", ["prompt", "1", "-o", str(work / "prompt.md")], expect=["Wrote"])
        (repo / "export.py").write_text("import csv\n\ndef export(rows, out):\n    csv.writer(out).writerows(rows)\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Add CSV export helper TM-1.1")
        r.run("sync marks subtask from commit", ["sync"], expect=["TM-1.1 done"])
        r.run("verify", ["verify", "1"], expect=["Verification", "Reviewed by"])
        r.run("verify --strict exits 0 or 1", ["verify", "1", "--strict"], expect_code=(0, 1))
        r.run("verify --base", ["verify", "1", "--base", "HEAD~1"], expect=["changes since HEAD~1"])
    if ai:  # steps only exist once `tm go` has made a plan
        r.run("done subtask", ["done", "1.2"], expect=["1.2"])
        r.run("undo subtask", ["done", "1.2", "--undo"], expect=["reopened"])
    r.run("done unknown subtask", ["done", "1.9"], expect_code=1, expect=["has no step 9"])
    r.run("done with trailing dot fails", ["done", "1."], expect_code=1)
    r.run("start", ["start", "1"], expect=["in progress"])
    r.run("edit", ["edit", "1", "--title", "Export notes as CSV", "-p", "medium"], expect=["Updated TM-1"])
    r.run("edit nothing fails", ["edit", "1"], expect_code=1, expect=["Nothing to change"])

    # Everyday flow
    r.run("add everyday task with reminder", ["add", "Plan a weekend trip with friends", "--no-repo",
                                              "--due", "tomorrow", "--remind", "+0m"],
          expect=["TM-2", "Reminder set"], reject=["repo:"])
    if ai:
        r.run("go everyday task", ["go", "2", "--no-questions"], expect=["Done when", "TM-2.1"],
              reject=["Security check", "Claude Code"])
    r.run("remind list", ["remind"], expect=["TM-2"])
    r.run("remind set", ["remind", "set", "1", "friday", "10:30"], expect=["reminder set"])
    r.run("remind bad time", ["remind", "set", "1", "someday"], expect_code=1)
    r.run("remind check sends once", ["remind", "check"], expect=["Reminder: Plan a weekend trip"])
    r.run("remind check again is quiet", ["remind", "check"], expect=["No reminders due"],
          reject=["Reminder: Plan"])
    r.run("calendar export", ["calendar", "export", "-o", str(work / "t.ics")], expect=["Exported"])
    r.run("calendar link", ["calendar", "link", "2"], expect=["calendar.google.com"])
    r.run("calendar link without dates", ["add", "No dates", "--no-repo"], expect=["TM-3"])
    r.run("calendar link fails cleanly", ["calendar", "link", "3"], expect_code=1, expect=["no due date"])
    r.run("today", ["today"], expect=["Today"])
    if ai:
        r.run("today --ai", ["today", "--ai"], expect=["Briefing"])
        r.run("sync --ai", ["sync", "--ai"], expect_code=0)

    # Simple mode
    r.run("simple mode on", ["config", "--mode", "simple"], expect=["simple"])
    r.run("simple wording", ["list"], expect=["Task 2"], reject=["TM-2", "inbox"])
    r.run("simple add never links repo", ["add", "Buy groceries"], expect=["Task 4"], reject=["repo:"])
    r.run("developer mode back", ["config", "--mode", "developer"], expect=["developer"])

    # Settings
    r.run("fallback and stage settings", ["config", "--fallback", "groq", "--stage", "clarify=groq",
                                          "--coder", "anthropic", "--review", "auto", "--remind-time", "08:30"],
          expect=["Saved"])
    r.run("bad stage fails cleanly", ["config", "--stage", "deploy=groq"], expect_code=1)
    r.run("clear settings", ["config", "--stage", "clarify=", "--coder", "none"], expect=["Saved"])
    r.run("voice status", ["voice"], expect=["speech to text", "text to speech"])
    r.run("import GitHub issues", ["import", "octocat/Hello-World", "--limit", "2", "--repo", str(repo)],
          expect_code=(0, 1), expect=[], reject=["Traceback"])
    r.run("rm", ["rm", "3", "-y"], expect=["Deleted TM-3"])

    failed = [x for x in r.results if not x[1]]
    total = sum(x[2] for x in r.results)
    print(f"\n{len(r.results) - len(failed)}/{len(r.results)} passed in {total:.0f}s. Files: {work}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
