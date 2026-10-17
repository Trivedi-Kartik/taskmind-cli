"""`tm build`: TaskMind does the task itself. It writes the code, runs it, tests it, fixes it and saves it.

Three promises, enforced by plain code rather than by asking the AI nicely:
  - Your code is safe: the work happens on a new git branch, and nothing reaches
    your branch until you say keep. Risky commands need your yes; dangerous ones never run.
  - Done means proven: TaskMind runs the check (your tests) itself and only calls the
    task done when it passes. If it fails, the errors go back to the AI to fix.
  - Nothing to babysit: one command from task to tested, saved change.

The engine has no terminal UI of its own (it reports through callbacks), so the
same loop can sit behind the CLI today and a web or chat front door later.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from taskmind.agent import TRIMMED
from taskmind.llm import LLMError, RequestTooLarge, ToolCallError
from taskmind.repo_tools import TOOL_SPECS as READ_TOOLS
from taskmind.repo_tools import Repo, dispatch
from taskmind.secrets import is_secret_file, redact

MAX_OUTPUT = 2500
MAX_WRITE = 60_000
COMMAND_TIMEOUT = 300
SHRUNK = "[already written; read the file to see it]"

# Commands that never run: they can damage the computer, or they take the
# branch and commits out of TaskMind's hands (it does those itself, safely).
BLOCKED = [
    (r"\bsudo\b|\bsu\s|\bdoas\b", "needs admin rights"),
    (r"\brm\s+(-\w+\s+)*(/|~|\$HOME|\.\.)(\s|/?$|/\*)", "deletes outside the project"),
    (r"\b(mkfs|fdisk|diskutil)\b|\bformat\s+[a-zA-Z]:|\bdd\s+if=|>\s*/dev/sd", "can erase a disk"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "turns the computer off"),
    (r":\(\)\s*\{", "a fork bomb"),
    (r"(curl|wget)[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b", "runs a script straight from the internet"),
    (r"\bgit\s+(push|reset|checkout|switch|commit|rebase|merge|branch|stash|clean|remote|config)\b",
     "TaskMind manages git itself"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "opens up system files"),
]
# Commands that run only after the user says yes (or with --yes).
ASK = [
    (r"\b(pip3?|uv|poetry|pipx|npm|pnpm|yarn|bun|gem|cargo|go|apt(-get)?|brew|dnf|yum|pacman|choco|winget)"
     r"\s+(install|add|get|i)\b", "installs software from the internet"),
    (r"\b(curl|wget|ssh|scp|rsync|nc|telnet)\b", "connects to the internet"),
    (r"\b(rm|rmdir|del|unlink|shred)\b", "deletes files"),
    (r"\b(kill|pkill|killall|taskkill)\b", "stops programs"),
    (r"\b(docker|podman|kubectl|terraform)\b", "manages containers or servers"),
]


def command_risk(command) -> tuple:
    """('ok' | 'ask' | 'block', reason) for a shell command the AI wants to run."""
    for pattern, reason in BLOCKED:
        if re.search(pattern, command):
            return "block", reason
    for pattern, reason in ASK:
        if re.search(pattern, command):
            return "ask", reason
    return "ok", ""


_KEY_NAMES = re.compile(r"(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_KEY)", re.I)


def clean_env() -> dict:
    """The environment for the AI's commands: the user's, minus every key and token."""
    env = {k: v for k, v in os.environ.items() if not _KEY_NAMES.search(k)}
    # No command may sit waiting for input that will never come.
    env.update(CI="1", GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               PIP_NO_INPUT="1", npm_config_yes="true")
    return env


def run_shell(command, cwd, timeout=COMMAND_TIMEOUT) -> tuple:
    """Run a command with no input and a time limit. Returns (exit code, output tail)."""
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    try:
        proc = subprocess.Popen(command, shell=True, cwd=str(cwd), env=clean_env(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    except OSError as exc:
        return 127, f"Could not start the command: {exc}"
    try:
        raw, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        _kill(proc)
        raw, _ = proc.communicate()
        code = 124
        raw = (raw or b"") + (f"\n[stopped after {timeout}s. Servers and watchers never finish; "
                              "test with a command that exits]").encode()
    text = redact((raw or b"").decode("utf-8", errors="replace"))
    if len(text) > MAX_OUTPUT:
        text = "... (start cut)\n" + text[-MAX_OUTPUT:]  # errors and summaries are at the end
    return code, text.strip() or "(no output)"


def _kill(proc):
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)  # the whole group: shells start children
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    except (OSError, ProcessLookupError):
        proc.kill()


def detect_check(root) -> str:
    """The project's own test command, if it has an obvious one."""
    root = Path(root)
    if (root / "package.json").exists():
        try:
            scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        test = scripts.get("test", "")
        if test and "no test specified" not in test:
            return "npm test"
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    has_py_tests = any(root.glob("test_*.py")) or any(root.glob("tests/test_*.py")) or any(root.glob("*_test.py"))
    if has_py_tests:
        py = Path(sys.executable).name if sys.executable else "python3"
        if shutil.which("pytest") or _has_module("pytest"):
            return f"{py} -m pytest -q"
        return f"{py} -m unittest discover -q" + (" -s tests" if (root / "tests").is_dir() else "")
    return ""


def _has_module(name) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


# Tools

WRITE_TOOLS = [
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create a file or replace its whole content. Use for new files and small files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative to the project root"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace one exact piece of text in a file. `old` must appear exactly once; "
                       "copy it from read_file without the line numbers. Prefer this for changes to big files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
        }, "required": ["path", "old", "new"]},
    }},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Run a shell command in the project root (no input, time-limited) and see its output. "
                       "Use it to run tests, scripts and linters. Never start servers or watchers.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Call when the task is complete and you have run the tests yourself.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "What you built and how to use it, for the user"},
            "check_command": {"type": "string",
                              "description": "One command that proves it works (runs the tests); exits 0 on success"},
        }, "required": ["summary", "check_command"]},
    }},
]
TOOLS = [t for t in READ_TOOLS if t["function"]["name"] != "recent_commits"] + WRITE_TOOLS


class Workspace:
    """The project folder, with the abilities the build agent gets: read, write, run."""

    def __init__(self, root, approve=None, timeout=COMMAND_TIMEOUT):
        self.repo = Repo(root)
        self.root = self.repo.root
        self.approve = approve or (lambda command, reason: False)
        self.timeout = timeout
        self.max_chars, self.max_lines = 3000, 120  # Groq-sized; build() widens them for roomy providers
        self.changed = set()   # files the AI wrote
        self.approved = set()  # commands the user already allowed in this build
        self.existing_tests = {p for p in self.repo.files() if _is_test(p)}

    def _target(self, rel) -> Path:
        path = self.repo._safe(rel)
        parts = path.relative_to(self.root).parts
        if not parts:
            raise ValueError("Give a file path, not the project folder")
        if ".git" in parts:
            raise ValueError("TaskMind manages .git itself")
        return path

    def write(self, rel, content) -> str:
        if len(content) > MAX_WRITE:
            raise ValueError(f"Too big for one write ({len(content)} characters); split it into smaller files")
        path = self._target(rel)
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        self._touched(path)
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"{'Replaced' if existed else 'Created'} {rel} ({lines} lines)."

    def edit(self, rel, old, new) -> str:
        path = self._target(rel)
        if not path.exists():
            raise ValueError(f"{rel} does not exist; use write_file to create it")
        if is_secret_file(str(path.relative_to(self.root))):
            raise ValueError(f"{rel} may hold secrets, so it can't be read or edited")
        text = path.read_text(encoding="utf-8", errors="replace")
        count = 0
        for old_try, new_try in _edit_variants(old, new):
            count = text.count(old_try) if old_try else 0
            if count == 1:
                break
        if count != 1:
            hint = ("not found. Read the file again and copy the exact text, without the line numbers"
                    if count == 0 else f"found {count} times. Include more surrounding lines so it's unique")
            raise ValueError(f"`old` text {hint}.")
        path.write_text(text.replace(old_try, new_try, 1), encoding="utf-8", newline="")
        self._touched(path)
        return f"Edited {rel}."

    def _touched(self, path):
        self.changed.add(path.relative_to(self.root).as_posix())
        self.repo._files = None  # the file list changed

    def run(self, command) -> str:
        risk, reason = command_risk(command)
        if risk == "block":
            return f"Not allowed: this command {reason}. Find another way."
        if risk == "ask" and command not in self.approved:
            if not self.approve(command, reason):
                return "The user said no to this command. Find another way, or finish without it."
            self.approved.add(command)
        code, output = run_shell(command, self.root, self.timeout)
        self.repo._files = None
        return f"exit code {code}\n{output}"

    def changed_tests(self) -> list:
        """Tests that existed before the build and were changed: worth a human look,
        since making tests pass by weakening them is the classic shortcut."""
        return sorted(self.changed & self.existing_tests)


_LINE_NUMBER = re.compile(r"^ *\d+ ?", re.M)


def _edit_variants(old, new):
    """The text as given, then forgiving readings of common model slips: line numbers
    copied from read_file, and trailing spaces. Each must still match exactly once."""
    yield old, new
    if all(_LINE_NUMBER.match(line) for line in old.split("\n") if line.strip()):
        numbered_new = all(_LINE_NUMBER.match(line) for line in new.split("\n") if line.strip())
        old = _LINE_NUMBER.sub("", old)
        new = _LINE_NUMBER.sub("", new) if numbered_new else new
        yield old, new
    yield "\n".join(line.rstrip() for line in old.split("\n")), new


def _is_test(path) -> bool:
    name = path.rsplit("/", 1)[-1]
    return bool(re.match(r"(test_.*|.*_test|.*\.(test|spec))\.\w+$", name)) or "/tests/" in f"/{path}"


def call_tool(ws: Workspace, name, args) -> str:
    try:
        if name == "write_file":
            return ws.write(args.get("path") or args.get("file") or "", str(args.get("content", "")))
        if name == "edit_file":
            return ws.edit(args.get("path") or args.get("file") or "",
                           str(args.get("old") or args.get("old_text") or ""),
                           str(args.get("new") if args.get("new") is not None else args.get("new_text", "")))
        if name == "run_command":
            command = str(args.get("command") or args.get("cmd") or "").strip()
            return ws.run(command) if command else "Give a command to run."
        return dispatch(ws.repo, name, args, max_chars=ws.max_chars, max_lines=ws.max_lines)
    except (ValueError, OSError, RuntimeError) as exc:
        return f"Tool error: {exc}"


# The agent loop

SYSTEM = """You are TaskMind's builder: a senior engineer who completes the user's task in their project, \
end to end, using tools. You don't describe the work; you do it.

Aim high: deliver what a skilled professional would ship for this request, not a minimal demo. \
If the request is short or vague, first decide what a real, complete version includes and build all of it \
(for a website: every section a real one has, real-sounding content, a mobile-friendly layout, accessible \
markup, and the polish asked for, such as modern animations).

How to work:
1. Look first: list_files, then read what the task touches. Follow the project's existing style.
2. Make the change with write_file (new or small files) or edit_file (changes to big files).
3. Add or update automated tests for the new behaviour, using the project's test setup \
(none? use the language's built-in one, e.g. Python unittest, with no installs).
4. Run the tests with run_command, read the errors, fix them, run again until they pass.
5. Call finish with a short summary and the one command that runs the tests.

Rules:
- Every command runs from the project root, with no keyboard input and a time limit. Don't start \
servers or watchers; prove things with tests or a short script that exits.
- Don't install packages unless the task truly needs them; prefer the standard library.
- Never touch secret files (.env, keys); use placeholders like os.environ["API_KEY"].
- TaskMind handles git (branch and commit). Don't run git commands that change anything.
- Don't weaken or delete existing tests to make them pass; fix the code instead.
- Keep files focused: split big work into several files (e.g. index.html, styles.css, script.js) and \
write them one per step; very long single files get cut off.
- Keep each reply short: call tools, don't explain."""


def _audience(language, simple) -> str:
    who = ("The user is not a programmer: in the finish summary, explain in plain everyday words what "
           "they can now do and how to open or run it (exact command), with no jargon."
           if simple else "In the finish summary, say what changed and how to use it, briefly.")
    if language in ("hindi", "hinglish"):
        who += (" Write the summary in Hinglish (Hindi in Latin letters, mixed with English)."
                if language == "hinglish" else " Write the summary in Hindi.")
    return who


def _msg_size(m) -> int:
    size = len(m.get("content") or "")
    for call in m.get("tool_calls") or []:
        size += len(call.get("function", {}).get("arguments") or "")
    return size


def _compact(messages, budget, keep=6):
    """Fit the conversation in the budget: shrink old tool outputs, then the content of
    old file writes (the files are on disk; the AI can read them back)."""
    def total():
        return sum(_msg_size(m) for m in messages)

    old = messages[2:-keep] if len(messages) > keep + 2 else []
    for m in old:
        if total() <= budget:
            return
        if m["role"] == "tool" and m["content"] != TRIMMED and len(m["content"]) > 200:
            m["content"] = TRIMMED
    for m in old:
        if total() <= budget:
            return
        for call in m.get("tool_calls") or []:
            fn = call.get("function", {})
            if fn.get("name") in ("write_file", "edit_file"):
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    continue
                for key in ("content", "old", "new"):
                    if isinstance(args.get(key), str) and len(args[key]) > 200:
                        args[key] = SHRUNK
                fn["arguments"] = json.dumps(args)
                m.pop("_raw", None)  # Claude's raw blocks would resend the full text


TOOL_NAMES = ", ".join(t["function"]["name"] for t in TOOLS)


def _finish_in_text(text):
    """finish's arguments written as a JSON reply instead of a tool call, or None."""
    from taskmind.llm import extract_object
    try:
        data = extract_object(text)
    except (ValueError, LLMError):
        return None
    return data if isinstance(data, dict) and ("summary" in data or "check_command" in data) else None


def _chat(llm, messages, budget, max_tokens):
    """One model turn. A garbled tool call (open models sometimes invent tools) is retried
    right here, so the build keeps its progress instead of starting over elsewhere."""
    failures = 0
    while True:
        _compact(messages, budget)
        try:
            return llm.chat(messages, tools=TOOLS, max_tokens=max_tokens)
        except RequestTooLarge:
            if budget <= 4000:
                raise
            budget //= 2
        except ToolCallError:
            failures += 1
            if failures >= 4:
                raise
            if failures == 2:
                messages.append({"role": "user", "content": f"Your last tool call was invalid. Call exactly "
                                                            f"one of these tools: {TOOL_NAMES}."})


def build(llm, task, ws: Workspace, check="", max_steps=40, fix_rounds=3, language="auto", simple=False,
          on_event=None, budget=None) -> dict:
    """Do the task in the workspace. Returns a report:

    status: "passed" (the check ran and passed), "failed" (the check kept failing),
            "unverified" (no check exists), or "unfinished" (out of steps).
    """
    event = on_event or (lambda kind, **info: None)
    budget = budget or max(getattr(llm, "context_budget", 9000), 9000)
    # Groq's free tier counts the reserved reply against 8k tokens a minute, so stay modest there.
    max_tokens = 4000 if budget <= 9000 else 8000 if budget < 100_000 else 16000
    if budget >= 100_000:  # e.g. Gemini, Claude: read whole files, write big ones
        ws.max_chars, ws.max_lines = 24_000, 800
    elif budget >= 30_000:
        ws.max_chars, ws.max_lines = 8000, 300
    known_check = check or detect_check(ws.root)
    goal = f"Task: {task}\n\nProject folder: {ws.root.name}\n\n{_audience(language, simple)}"
    if known_check:
        goal += f"\n\nThe project's tests run with: {known_check}. They must pass when you finish."
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": goal}]
    report = {"status": "unfinished", "summary": "", "check": known_check, "check_output": "", "steps": 0,
              "rounds": 0}
    nudged = False
    for step in range(max_steps):
        report["steps"] = step + 1
        reply = _chat(llm, messages, budget, max_tokens)
        calls = reply.get("tool_calls") or []
        finished = None
        if not calls:
            # Open models sometimes stop to talk, or write finish's arguments as text instead of
            # calling it. Either way the check decides: a finish in text counts, and so does
            # stopping a second time.
            text = reply.get("content") or ""
            messages.append({"role": "assistant", "content": text})
            finished = _finish_in_text(text)
            if finished is None and not nudged:
                nudged = True
                messages.append({"role": "user", "content": "Keep going with the tools until the task is "
                                 "done and tested, then call finish."})
                continue
            finished = finished or {"summary": text}
            messages.append({"role": "user", "content": "Checking..."})
            calls = []
        if calls:
            assistant = {"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls}
            if reply.get("_raw") is not None:
                assistant["_raw"] = reply["_raw"]
            messages.append(assistant)
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            if name == "finish":
                finished = args
                result = "Checking..."
            else:
                event("tool", name=name, args=args)
                result = call_tool(ws, name, args)
                event("result", name=name, args=args, result=result)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
        left = max_steps - step - 1
        if left == 3 and finished is None:
            messages.append({"role": "user", "content": "3 steps left: get the tests passing and call finish."})
        if finished is None:
            continue

        report["summary"] = str(finished.get("summary") or "")
        # The project's tests may only exist now (the AI wrote them), so look again.
        # TaskMind's own detection comes before the AI's command: an AI command that finds no
        # tests (seen live: unittest exit code 5) sends it off "fixing" code that works.
        command = known_check or detect_check(ws.root) or str(finished.get("check_command") or "").strip()
        if not command:
            report["status"] = "unverified"
            break
        if command_risk(command)[0] != "ok":
            report.update(status="unverified", check=command,
                          check_output="TaskMind won't run this check by itself: " + command_risk(command)[1])
            break
        report["check"] = command
        event("check", command=command)
        code, output = run_shell(command, ws.root, ws.timeout)
        report["check_output"] = output
        event("checked", command=command, code=code, output=output)
        if code == 0:
            report["status"] = "passed"
            break
        report["rounds"] += 1
        if report["rounds"] > fix_rounds:
            report["status"] = "failed"
            break
        messages[-1]["content"] = (f"Not done: TaskMind ran `{command}` and it failed (exit code {code}):\n"
                                   f"{output}\n\nFix the code (not the tests, unless they are wrong), "
                                   "run the tests, then call finish again.")
    report["changed"] = sorted(ws.changed)
    report["changed_tests"] = ws.changed_tests()
    return report


# Git: a branch for the work, a commit for the result

class GitError(Exception):
    pass


def git(root, *args, check=True) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
    except FileNotFoundError as exc:
        raise GitError("git is not installed. Install it from https://git-scm.com and try again.") from exc
    if check and result.returncode != 0:
        raise GitError((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def _commit(root, *args):
    """Commit as the user; if git doesn't know who they are yet, sign as TaskMind."""
    if git(root, "config", "user.email", check=False):
        return git(root, "commit", "-q", "--no-verify", *args)
    return git(root, "-c", "user.name=TaskMind", "-c", "user.email=taskmind@localhost",
               "commit", "-q", "--no-verify", *args)


def slug(text, limit=40) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return words[:limit].rstrip("-") or "task"


class NotAProject(GitError):
    """The folder isn't a git project and has files in it (e.g. the home folder)."""


def is_project(root) -> bool:
    return git(root, "rev-parse", "--is-inside-work-tree", check=False) == "true"


FILLER = {"please", "create", "make", "build", "write", "add", "a", "an", "the", "one", "me", "my", "i", "want",
          "need", "to", "for", "of", "with", "and", "that", "can", "you", "some", "new", "simple", "mujhe", "ek",
          "chahiye", "ke", "liye", "ka", "ki", "meri", "mera"}


def project_name(task) -> str:
    """A short folder name from the task: 'Please create a coffee cafe website...' -> 'coffee-cafe-website'."""
    words = [w for w in re.findall(r"[a-z0-9]+", task.lower()) if w not in FILLER]
    return slug(" ".join(words[:3]), 30) if words else "my-project"


def start_branch(root, task, new_project=False) -> tuple:
    """Put the work on a fresh branch. Returns (the user's branch, the new branch).

    Git starts only in a new project or an empty folder: never in a folder that already
    holds other things (the home folder, Downloads), which would sweep them all in."""
    if new_project or (not is_project(root) and not any(Path(root).iterdir())):
        git(root, "init", "-q")
    elif not is_project(root):
        raise NotAProject(f"{root} isn't a project folder.")
    if git(root, "status", "--porcelain"):
        raise GitError("This project has changes that aren't committed yet. Commit them first "
                       "(git add -A && git commit -m \"...\"), so TaskMind's work stays separate from yours.")
    if not git(root, "rev-parse", "--verify", "-q", "HEAD", check=False):
        _commit(root, "--allow-empty", "-m", "Start")  # a branch needs a commit to start from
    base = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    existing = set(git(root, "branch", "--format=%(refname:short)").split())
    name, n = f"tm/{slug(task)}", 2
    while name in existing:
        name, n = f"tm/{slug(task)}-{n}", n + 1
    git(root, "checkout", "-q", "-b", name)
    return base, name


def save_work(root, message) -> list:
    """Commit everything the build produced, except secret files. Returns the files committed."""
    git(root, "add", "-A")
    staged = [p for p in git(root, "diff", "--cached", "--name-only", "-z").split("\0") if p]
    secret = [p for p in staged if is_secret_file(p)]
    if secret:
        git(root, "reset", "-q", "--", *secret)
    files = [p for p in staged if p not in secret]
    if files:
        _commit(root, "-m", message)
    return files


def keep(root, base, branch):
    """Bring the branch's work into the user's branch and tidy up."""
    git(root, "checkout", "-q", base)
    git(root, "merge", "-q", "--ff-only", branch)
    git(root, "branch", "-q", "-d", branch)


def discard(root, base, branch):
    git(root, "checkout", "-q", "-f", base)
    git(root, "branch", "-q", "-D", branch)
