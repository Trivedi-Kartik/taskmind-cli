"""`tm` / `tm chat`: one session in a project, like talking to a colleague at the terminal.

Say what you want built or changed and it's built, checked and kept (`/undo` takes it back). Ask a
question and it's answered from the code, without changing anything. Follow-ups just work ("make that
button bigger" knows which button), because the session remembers what it has done. Everything runs
through the same commands as `tm build`, so the same safety applies: a branch for the work, a yes for
risky commands, checks before anything counts as done.
"""

import os
import re
import shlex
import sys
from pathlib import Path

from taskmind import __version__, builder, config, insights, project, routing, ui
from taskmind.core import CLIError
from taskmind.llm import LLMError
from taskmind.repo_tools import Repo

COMMANDS = {
    "/help": "this list",
    "/undo": "take back the last build",
    "/diff": "show what the last build changed",
    "/status": "where things stand: folder, branch, AI, the builds so far",
    "/ask": "answer a question (/ask where is the login handled?)",
    "/build": "build this, even if it sounds like a question",
    "/new": "start a new project in a folder (/new my-shop a billing program)",
    "/clear": "forget this conversation; the next request starts fresh (your code stays as it is)",
    "/init": "study the project and write TASKMIND.md",
    "/index": "show the code index",
    "/voice": "say your request instead of typing it",
    "/model": "which AI is being used",
    "/exit": "leave (also Ctrl+D)",
}

_QUESTION = re.compile(
    r"^(how|what|whats|what's|where|why|which|who|when|does|do you|do we|is|are|explain|describe|tell me|"
    r"walk me through|summari[sz]e|review|(?:can|could|would) you (?:explain|tell|show|describe|walk|summari[sz]e))\b",
    re.I)
_REQUEST = re.compile(r"^(please|pls)\b|^(can|could|would|will) you (?!explain|tell|show|describe|walk|summari)"
                      r"|^(i want|i need|i'd like|let's|lets)\b", re.I)


def classify(text) -> str:
    """'ask' for a question about the code, 'build' for a request to change or make something. Politely
    worded requests ("can you add ...") are builds; `/ask` and `/build` override this."""
    text = text.strip()
    if _REQUEST.match(text):
        return "build"
    if _QUESTION.match(text) or text.endswith("?"):
        return "ask"
    return "build"


class Session:
    def __init__(self, app):
        self.app = app
        self.root = Path(os.getcwd())
        self.built = False      # has this session built something here (so the next request is a follow-up)
        self.pending_new = None  # /new: the next build starts a project in this folder
        self.memory = []         # recent questions and answers, for follow-up questions

    # screen

    def project_facts(self):
        root = self.root
        if not builder.is_project(root):
            return "not a project folder yet", ""
        repo = Repo(root)
        files = repo.code_file_count()
        notes = [name for name in project.NOTE_FILES if (root / name).is_file()]
        from taskmind.codeindex import MAP_MIN_FILES
        return (f"🌿 {builder.current_branch(root)}",
                "  ".join(([f"📘 {', '.join(notes)}"] if notes else []) +
                          ([f"🗺 {files:,} code files"] if files >= MAP_MIN_FILES else [])))

    def welcome(self, cfg):
        name = insights.profile(cfg)["name"]
        cands = routing.candidates(cfg, "build")
        ai = routing.describe(cands[0]) if cands else "no AI set up (run tm setup)"
        branch, extras = self.project_facts()
        lines = [f"📁 {ui.escape(self.root.name)}   {ui.escape(branch)}   🤖 {ui.escape(ai)}"]
        if extras:
            lines.append(ui.escape(extras))
        lines += ["", (f"Hi {ui.escape(name)}! " if name else "") +
                  "Say what you want built or changed, or ask a question about the code.",
                  "[dim]/help for commands · Ctrl+C stops a build · Ctrl+D leaves[/dim]"]
        ui.panel(f"TaskMind {__version__}", lines)

    # reading input

    def setup_readline(self):
        if not sys.stdin.isatty():
            return None
        try:
            import atexit
            import readline
        except ImportError:  # Windows: no history or completion, still fully usable
            return None
        history = config.home() / "chat-history"
        try:
            readline.read_history_file(str(history))
        except OSError:
            pass
        readline.set_history_length(500)
        atexit.register(lambda: _save_history(readline, history))

        def complete(text, state):
            options = [c for c in COMMANDS if c.startswith(text)] if text.startswith("/") else []
            return options[state] if state < len(options) else None

        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")
        return readline

    def read(self):
        """The next request, or None when input ends. A trailing backslash continues the line."""
        try:
            line = input(ui.chat_prompt())
            while line.endswith("\\"):
                line = line[:-1] + "\n" + input("... ")
            return line
        except EOFError:
            return None

    # one request at a time

    def run(self) -> int:
        cfg = self.app.ensure_setup()
        self.setup_readline()
        self.welcome(cfg)
        while True:
            try:
                line = self.read()
            except KeyboardInterrupt:
                ui.out("\n[dim](Ctrl+D or /exit leaves)[/dim]")
                continue
            if line is None:
                break
            if not self.handle(line.strip()):
                break
        self.goodbye()
        return 0

    def handle(self, line) -> bool:
        """Do what one line asks. False means the session is over."""
        if not line:
            return True
        if line.lower() in ("exit", "quit", "bye", "/exit", "/quit"):
            return False
        try:
            if line.startswith("/"):
                return self.command(line)
            if re.match(r"^tm\s", line):  # any TaskMind command still works: `tm list`
                self.app.main(shlex.split(line)[1:])
                return True
            if re.search(r"\btm\b|taskmind", line, re.I) and classify(line) == "ask":
                self.app.main(["ask", line])  # a question about TaskMind itself
                return True
            (self.ask if classify(line) == "ask" else self.build)(line)
        except KeyboardInterrupt:
            ui.out("\n[yellow]Stopped.[/yellow] Anything already done is saved; say what to change to carry on, "
                   "or /undo.")
        return True

    def command(self, line) -> bool:
        word, _, rest = line.partition(" ")
        word, rest = word.lower(), rest.strip()
        if word == "/help":
            ui.out("[bold]Just type:[/bold] what you want built or changed, or a question about the code.")
            for name, text in COMMANDS.items():
                ui.out(f"  [cyan]{name:<9}[/cyan] {ui.escape(text)}")
            ui.out("  [cyan]tm ...[/cyan]    run any TaskMind command (tm list, tm index --find name)")
            ui.out("  [dim]End a line with a backslash to continue it on the next line.[/dim]")
        elif word == "/undo":
            self.app.main(["build", "--undo"])
            self.built = False
        elif word == "/diff":
            self.diff()
        elif word == "/status":
            self.status()
        elif word == "/ask":
            self.ask(rest) if rest else ui.out("Ask something: /ask where is the login handled?")
        elif word == "/build":
            self.build(rest) if rest else ui.out("Say what to build: /build add a dark mode")
        elif word == "/new":
            self.new(rest)
        elif word == "/clear":
            self.built, self.memory = False, []
            ui.out("[dim]Forgotten. The next request starts fresh; your code stays as it is.[/dim]")
        elif word == "/init":
            self.app.main(["init"])
        elif word == "/index":
            self.app.main(["index"])
        elif word == "/voice":
            return self.handle(self.app.listen().strip())
        elif word == "/model":
            self.model()
        else:
            ui.out(f"[yellow]Unknown command {ui.escape(word)}.[/yellow] /help lists them.")
        return True

    def build(self, text):
        argv = ["build", "--chat", "--auto-keep"]
        if self.pending_new:
            argv += ["--new", self.pending_new]
        elif self.built and builder.is_project(self.root) and builder.last_build(self.root):
            argv.append("--more")  # the session remembers: this continues the earlier work
        self.app.LAST_BUILD_ROOT = None
        code = self.app.main(argv + ["--", text])
        built_in = self.app.LAST_BUILD_ROOT
        if built_in and Path(built_in) != self.root:  # a new project was made: the session moves into it
            self.root = Path(built_in)
            os.chdir(self.root)
            ui.out(f"[dim]Now working in {ui.escape(str(self.root))}[/dim]")
        if built_in:
            self.built = True
            self.pending_new = None
        return code

    def ask(self, text):
        if not builder.is_project(self.root):
            ui.out("This folder isn't a project, so there's no code to ask about. Say what you want built and I'll "
                   "make a new folder for it, or cd into a project.")
            return
        try:
            cfg = self.app.ensure_setup()
            cands = routing.candidates(cfg, "analyze")
            repo = Repo(self.root)
            self.app.ensure_code_consent(repo, "analyze", cands)

            def on_step(name, args):
                detail = args.get("name") or args.get("pattern") or args.get("path") or ""
                ui.out(f"  [dim]→ {name.replace('_', ' ')} {ui.escape(str(detail))}[/dim]")

            answer = self.app.AI("analyze", cands=cands).run(
                lambda llm: project.answer_code_question(llm, repo, text, self.memory, on_step=on_step))
        except (CLIError, LLMError) as exc:
            ui.error(str(exc))
            return
        ui.out("")
        ui.markdown(answer or "I couldn't find an answer in the code.")
        self.memory.append((text, answer))

    def new(self, rest):
        folder, _, request = rest.partition(" ")
        if not folder:
            ui.out("Name a folder: /new my-shop a billing program for a small shop")
            return
        self.pending_new = folder
        if request.strip():
            self.build(request.strip())
        else:
            ui.out(f"[dim]The next request starts a new project in {ui.escape(folder)}.[/dim]")

    def diff(self):
        try:
            stat, patch = builder.last_diff(self.root)
        except builder.GitError as exc:
            ui.out(f"[yellow]{ui.escape(str(exc))}[/yellow]")
            return
        ui.plain(stat)
        shown = "\n".join(patch.splitlines()[:300])
        ui.diff(shown)
        if len(patch.splitlines()) > 300:
            ui.out("[dim]... (shortened; git diff shows all of it)[/dim]")

    def status(self):
        branch, extras = self.project_facts()
        ui.out(f"📁 {ui.escape(str(self.root))}   {ui.escape(branch)}")
        state = builder.last_build(self.root) if builder.is_project(self.root) else None
        rounds = builder.rounds_of(state) if state else []
        if not rounds:
            ui.out("[dim]No builds in this folder yet.[/dim]")
            return
        ui.out("Builds in this folder (latest last):")
        for r in rounds[-5:]:
            result = builder.ROUND_RESULT.get(r.get("status"), r.get("status") or "")
            ui.out(f"  • {ui.escape((r.get('request') or '')[:70])} → {ui.escape(result)}")
        ui.out(f"[dim]{'Kept on ' + state['base'] if state.get('kept') else 'On branch ' + state['branch']}[/dim]")

    def model(self):
        cands = routing.candidates(config.load(), "build")
        if not cands:
            ui.out("No AI is set up. Run tm setup.")
            return
        ui.out(f"Building with {ui.escape(routing.describe(cands[0]))}")
        if len(cands) > 1:
            ui.out("Backups: " + ui.escape(", ".join(routing.describe(c) for c in cands[1:])))
        ui.out("[dim]Change it: tm config --stage build=PROVIDER[:MODEL]  (for example gemini or anthropic)[/dim]")

    def goodbye(self):
        state = builder.last_build(self.root) if builder.is_project(self.root) else None
        if state and not state.get("kept") and builder.leftover_build(self.root):
            ui.out(f"\n[yellow]Unfinished work is saved on branch {ui.escape(state['branch'])}.[/yellow] "
                   "Next time: say what to change, or /undo to throw it away.")
        ui.out("[dim]Bye![/dim]")


def _save_history(readline, path):
    try:
        readline.write_history_file(str(path))
    except OSError:
        pass


def run(app) -> int:
    return Session(app).run()
