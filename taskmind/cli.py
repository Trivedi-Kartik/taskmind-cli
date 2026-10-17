"""`tm`: the TaskMind command line."""

import argparse
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from taskmind import (__version__, config, diagnostics, github, helpdocs, insights, reminders,
                      routing, stages, tracking, tutorial, ui, verify, voice)
from taskmind.db import PRIORITIES, STATUSES, close_all
from taskmind.db import now as db_now
from taskmind import llm as llm_client
from taskmind.llm import LLMError
from taskmind.core import BACKUPS_KEPT  # noqa: F401  (kept for `cli.BACKUPS_KEPT`)
from taskmind.core import (CLIError, copy_to_clipboard, detect_repo, get_task, interactive, open_db, progress,
                           resolve_repo, task_repo)
from taskmind.display import (finished_titles, key_status,
                              show_analysis, show_go_summary, show_plan, show_settings, show_verification,
                              simple_mode, spoken_summary, stage_banner, status_label)
from taskmind.parsing import (QUESTION_WORDS, REF_RE, FriendlyParser, UsageError, closest_command,
                              friendly_usage_error, looks_like_a_sentence, parse_due, parse_ref)
from taskmind.personal import (add_to_wishlist, cmd_backup, cmd_export, cmd_feedback, cmd_greet, cmd_me,
                               cmd_report_bug, cmd_week, cmd_wishlist, maybe_greet)
from taskmind.planner import cmd_calendar, cmd_remind


# Helpers


def ensure_setup():
    """Offer guided setup when no provider is ready; return the loaded config."""
    cfg = config.load()
    if routing.candidates(cfg, "clarify"):
        return cfg
    s = config.llm_settings(cfg)
    if sys.stdin.isatty() and ui.confirm("TaskMind isn't connected to an AI yet. Set it up now?", True):
        run_setup()
        return config.load()
    missing = "a model" if s["api_key"] or not s["needs_key"] else "an API key"
    raise CLIError(f"{s['label']} needs {missing}. Run `tm setup` "
                   f"(get a key at {s['signup']}, or export {s['key_env']}).")


def ensure_code_consent(repo, stage, cands=None):
    """Before a project's code first goes to an AI provider, say so and ask once per project."""
    cfg = config.load()
    root = str(repo.root)
    if root in cfg.get("projects_ok", []):
        return
    cands = cands if cands is not None else routing.candidates(cfg, stage)
    names = ", ".join(dict.fromkeys(c["label"] for c in cands)) or "your AI provider"
    message = (f"TaskMind will send parts of the code in [bold]{ui.escape(repo.root.name)}[/bold] to "
               f"{ui.escape(names)} to understand it. Files with secrets (.env, keys, credentials) are never "
               "sent, and keys inside code are blanked out.")
    if not interactive():
        ui.out(f"[dim]{message}[/dim]")  # scripts: say it, don't block
        return
    ui.out(message)
    if not ui.confirm("Allow this for this project?", default=True):
        raise CLIError("Okay, nothing was sent. To plan without reading code, unlink the project with "
                       "`tm edit <id> --repo \"\"`, then run `tm go <id>`.")
    cfg.setdefault("projects_ok", []).append(root)
    config.save(cfg)


class AI:
    """Runs one stage's AI calls on the right provider, failing over to backups.

    Each call is retried from scratch on the next provider; stages save their
    results, so a failure never loses finished work.
    """

    def __init__(self, stage, task=None, cands=None):
        self.stage = stage
        self.task = task
        self.cands = cands if cands is not None else routing.candidates(ensure_setup(), stage)
        self.used = None

    def run(self, fn):
        def on_switch(current, nxt, exc):
            reason = str(exc).splitlines()[0][:160]
            ui.out(f"[yellow]⚠ {ui.escape(routing.describe(current))}: {ui.escape(reason)}[/yellow]")
            ui.out(f"[yellow]→ Continuing with {ui.escape(routing.describe(nxt))}[/yellow]")

        result, self.used = routing.run(self.cands, fn, on_switch)
        if self.task is not None:
            self.record()
        return result

    def record(self):
        db = open_db()
        log = db.get_task(self.task["id"]).get("ai_log") or {}
        log[self.stage] = {"provider": self.used["provider"], "model": self.used["model"]}
        db.update_task(self.task["id"], ai_log=log)


def make_llm():
    """The main provider's client, without failover (used by `tm doctor`)."""
    return llm_client.from_settings(config.llm_settings(ensure_setup()))


# Voice

def listen():
    """Record from the microphone until Enter, then return the transcript."""
    if not sys.stdin.isatty():
        raise CLIError("Voice input needs an interactive terminal.")
    ui.out("[bold red]🎙  Listening...[/bold red] press Enter when you're done")
    path = voice.record(wait_for_stop=lambda: ui.ask(""))
    try:
        with ui.working("Transcribing..."):
            text = voice.transcribe(config.load(), path)
    finally:
        path.unlink(missing_ok=True)
    if not text:
        raise voice.VoiceError("I didn't catch anything. Try again a little closer to the microphone.")
    ui.plain(f'  You said: "{text}"')
    return spoken_command(text)


def spoken_command(text) -> str:
    """People say the command name too ("TM list", "T.M. add buy milk"); drop it and end punctuation."""
    text = re.sub(r"^\s*(t\.?\s?m\.?|tee em|taskmind)[\s,.:]+", "", text, flags=re.I)
    return text.strip().rstrip(".!")


def make_speaker():
    return voice.Speaker(config.load(), notify=lambda m: ui.out(f"[yellow]{ui.escape(m)}[/yellow]"))


def voice_answer():
    """Answer typed, or spoken when the user just presses Enter."""
    typed = ui.ask("> (type an answer, press Enter to speak, or 'skip'):")
    if typed:
        return "" if typed.lower() == "skip" else typed
    try:
        return listen()
    except voice.VoiceError as exc:
        ui.out(f"[yellow]{ui.escape(str(exc))}[/yellow]")
        return ui.ask("> Type your answer instead (Enter to skip):")


# Stage runners (shared by individual commands and `tm go`)


def run_clarify(db, task, ask_questions=True, use_voice=False, step=None):
    ai = AI("clarify", task)
    speaker = make_speaker() if use_voice else None
    repo = task_repo(task)
    if repo:
        ensure_code_consent(repo, "clarify")
    overview = repo.overview() if repo else None
    clarifications = list(task.get("clarifications") or [])
    stage_banner(step, "Understanding your task",
                 "First I make sure the task is clear: I ask what's missing, then write down exactly "
                 "what \"done\" means.")
    if ask_questions:
        with ui.working("Thinking about what's unclear..."):
            questions = ai.run(lambda llm: stages.clarify_questions(llm, task, overview))
        if questions:
            ui.heading(f"{len(questions)} questions")
            ui.out("[dim]" + ("Press Enter and speak, type an answer, or type 'skip'." if use_voice else
                    "Answer what you know. Press Enter to skip a question and I'll make a sensible "
                    "assumption instead.") + "[/dim]")
            if speaker:
                speaker.say(f"I have {len(questions)} questions before we start.")
            for q in questions:
                ui.plain(f"\n? {q}")
                if speaker:
                    speaker.say(q)
                answer = voice_answer() if use_voice else ui.ask(">")
                clarifications.append({"q": q, "a": answer})
            db.update_task(task["id"], clarifications=clarifications)
            task = db.get_task(task["id"])
        else:
            ui.out("The task is clear, no questions needed.")
    with ui.working("Writing the task definition..."):
        result = ai.run(lambda llm: stages.write_definition(llm, task, overview))
    db.update_task(task["id"], definition=result["definition"], acceptance=result["acceptance"],
                   status=_advance(task, "clarified"))
    db.log(task["id"], "clarified", "Task definition written")
    task = db.get_task(task["id"])
    skipped = sum(1 for c in task.get("clarifications") or [] if not c.get("a"))
    ui.heading("The task, written down clearly")
    if skipped:
        ui.out(f"[dim]You skipped {skipped} question(s), so I made assumptions; check them below.[/dim]")
    ui.markdown(task["definition"])
    ui.bullets("Done when (acceptance criteria)", task["acceptance"])
    if speaker:
        speaker.say(f"Got it. I wrote the task definition with {len(task['acceptance'])} acceptance criteria.")
    return task


def run_analyze(db, task, step=None):
    repo = task_repo(task)
    ai = AI("analyze", task)
    if repo:
        ensure_code_consent(repo, "analyze")
        stage_banner(step, "Reading your code",
                     f"I search and read the code in {repo.root.name} to find what this task touches, what "
                     "could go wrong, and security risks. Each → line is one search or file I looked at.")
    else:
        stage_banner(step, "Thinking it through",
                     "What you'll need, what could block you, and what people usually forget.")

    def on_step(name, args):
        detail = args.get("pattern") or args.get("path") or args.get("reason") or ""
        ui.out(f"  [dim]→ {name} {ui.escape(str(detail))}[/dim]")

    analysis = ai.run(lambda llm: stages.analyze(llm, task, repo, on_step=on_step))
    db.update_task(task["id"], analysis=analysis, status=_advance(task, "analyzed"))
    db.log(task["id"], "analyzed", f"{len(analysis['affected_files'])} files, "
                                   f"{len(analysis['edge_cases'])} edge cases")
    task = db.get_task(task["id"])
    show_analysis(analysis)
    return task


def run_plan(db, task, step=None):
    ai = AI("plan", task)
    stage_banner(step, "Making a plan",
                 "Small steps you can do and commit one at a time, plus the tests that prove it works."
                 if task.get("repo") else "Small steps you can each do in one sitting, in the right order.")
    if not task.get("analysis"):
        ui.out("[dim]No analysis yet; planning from the definition only.[/dim]")
    with ui.working("Planning subtasks and test cases..." if task.get("repo") else "Planning the steps..."):
        preferences = insights.preference_guidance(db)
        result = ai.run(lambda llm: stages.plan(llm, task, task.get("analysis") or {}, preferences))
    if preferences:
        ui.out("[dim]Planned the way you like (learned from your earlier edits; see `tm me`).[/dim]")
    if not result["subtasks"]:
        raise CLIError("The model returned no subtasks. Try `tm plan` again.")
    db.set_subtasks(task["id"], result["subtasks"])
    db.update_task(task["id"], planned_at=db_now(), tests=result["tests"], priority=result["priority"],
                   estimate_hours=result["estimate_hours"], status=_advance(task, "planned"))
    db.log(task["id"], "planned", f"{len(result['subtasks'])} subtasks")
    task = db.get_task(task["id"])
    show_plan(db, task)
    return task


def _advance(task, status):
    """Move a task forward through the pipeline, never backwards."""
    if STATUSES.index(task["status"]) >= STATUSES.index(status):
        return task["status"]
    return status


# Display


# Commands

# Model lists include embeddings, speech, image and safety models; hide those.
NON_CHAT = re.compile(r"embed|tts|whisper|dall-e|image|audio|moderation|realtime|transcri|speech|"
                      r"guard|orpheus|safeguard|rerank|search|davinci|babbage", re.I)


def chat_models(ids):
    return [m for m in ids if not NON_CHAT.search(m)]


def test_connection(llm):
    reply = llm.chat([{"role": "user", "content": "Reply with exactly: TaskMind is ready"}], max_tokens=300)
    return (reply.get("content") or "").strip()


def run_setup():
    """Interactive: pick a provider, add its key, pick a model, test it, save."""
    cfg = config.load()
    names = list(config.PROVIDERS)
    ui.heading("Choose an AI provider")
    for i, name in enumerate(names, start=1):
        spec = config.PROVIDERS[name]
        mark = "  ← current" if name == cfg["provider"] else ""
        ui.plain(f"  {i}. {spec['label']:<20} {spec['cost']}{mark}")
    default = names.index(cfg["provider"]) + 1
    answer = ui.ask(f"Number [{default}]:") or str(default)
    if not answer.isdigit() or not 1 <= int(answer) <= len(names):
        raise CLIError(f"Pick a number from 1 to {len(names)}.")
    provider = names[int(answer) - 1]
    s = config.llm_settings(cfg, provider)
    if s["kind"] == "anthropic":
        try:
            import anthropic  # noqa: F401  (only checking it's installed)
        except ImportError as exc:
            raise CLIError("Claude needs Anthropic's official SDK. Install it with "
                           "`pip install anthropic`, then run `tm setup` again.") from exc

    if s["key_env"]:
        ui.out(f"\nGet a {s['label']} API key at {s['signup']}")
        if s["api_key"]:
            hint = "Press Enter to keep the current key"
        elif s.get("key_optional"):
            hint = f"Press Enter to use {s['key_env']} or an `ant auth login` profile"
        else:
            hint = "The key is hidden as you paste it"
        key = ui.secret(f"Paste your API key ({hint}):")
        if key:
            s["api_key"] = key
        elif s["needs_key"] and not s["api_key"]:
            raise CLIError(f"{s['label']} needs an API key.")

    probe = llm_client.from_settings({**s, "model": s["model"] or "-"})
    try:
        with ui.working(f"Fetching {s['label']} models..."):
            models = chat_models(probe.list_models())
    except LLMError as exc:
        ui.out(f"[yellow]Could not list models:[/yellow] {ui.escape(str(exc))}")
        models = []
    suggested = s["model"] if s["model"] in models else None
    if models:
        ui.heading(f"Choose a model ({len(models)} available)")
        for i, m in enumerate(models[:40], start=1):
            ui.plain(f"  {i}. {m}{'  ← recommended' if m == suggested else ''}")
        if len(models) > 40:
            ui.plain(f"  ... and {len(models) - 40} more (type any model id)")
    prompt = f"Number or model id [{suggested}]:" if suggested else "Number or model id:"
    answer = ui.ask(prompt) or suggested
    if not answer:
        raise CLIError("No model chosen.")
    model = models[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= min(len(models), 40) else answer

    final = {**s, "model": model}
    try:
        with ui.working(f"Testing {model}..."):
            reply = test_connection(llm_client.from_settings(final))
        ui.out(f"[green]Connected.[/green] {ui.escape(reply)}")
    except LLMError as exc:
        ui.out(f"[red]Test failed:[/red] {ui.escape(str(exc))}")
        if not ui.confirm("Save these settings anyway?"):
            return
    if "mode" not in cfg:
        codes = ui.confirm("\nDo you write code? (No switches on simple mode with plain wording)", default=True)
        cfg["mode"] = "developer" if codes else "simple"
    if not (cfg.get("profile") or {}).get("name"):
        name = ui.ask("What should I call you? (Enter to skip)")
        if name:
            cfg["profile"] = {**insights.profile(cfg), "name": name}
    cfg["provider"] = provider
    cfg["models"][provider] = model
    if provider not in cfg["fallback"]:
        cfg["fallback"].append(provider)  # every provider you set up becomes a backup
    if s["api_key"] and s["key_env"] and s["api_key"] != os.environ.get(s["key_env"]):
        cfg["keys"][provider] = s["api_key"]
    config.save(cfg)
    ui.out("[green]Saved.[/green]")
    show_settings(cfg)
    if len(cfg["fallback"]) < 2:
        ui.out("[dim]Tip: run `tm setup` again to add a second provider. It becomes an automatic "
               "backup when limits run out, and an independent reviewer for `tm verify`.[/dim]")


def cmd_voice(args):
    cfg = config.load()
    speaker = make_speaker()
    stt = voice.stt_provider(cfg)
    recorder = voice.recorder_command("test.wav")
    player = voice.player_command("test.wav")
    ui.table(["Voice", "Status"], [
        ["microphone recorder", recorder[0] if recorder else "MISSING (install alsa-utils, sox or ffmpeg)"],
        ["audio player", player[0] if player else "missing (only needed for Groq voices)"],
        ["speech to text", f"{config.PROVIDERS[stt[0]]['label']} / {stt[3]}" if stt
         else "needs a Groq (free) or OpenAI key: run `tm setup`"],
        ["text to speech", speaker.describe()],
    ])
    if speaker.engine != "groq" and speaker.mode == "auto" and not speaker.groq_key:
        ui.out("[dim]Set up Groq (free) for a natural voice.[/dim]")
    if not args.test:
        ui.out("[dim]Run `tm voice --test` to try speaking and listening.[/dim]")
        return
    with ui.working("Speaking..."):
        speaker.say("Hi, this is TaskMind. Your voice setup is working.")
    if ui.confirm("Test the microphone now (it records until you press Enter)?", default=True):
        text = listen()
        speaker.say(f"I heard: {text}")
        ui.out("[dim]That was only a microphone test. To give commands by voice, use `tm talk` "
               "(hands-free) or `tm do --voice` (one request).[/dim]")
        if text and ui.confirm(f'Do it now as a request ("{text}")?', default=False):
            return main(["do", text])


def current_state(db) -> str:
    """What `tm ask` may know about the user's setup and tasks. Never includes API keys."""
    cfg = config.load()
    s = config.llm_settings(cfg)
    lines = [
        f"AI provider: {s['label']} ({s['cost']}), model {s['model'] or 'not chosen'}, "
        f"API key {key_status(s)}",
        f"Backups: {', '.join(cfg.get('fallback') or []) or 'none'}; mode: {cfg.get('mode') or 'developer'}",
        "Providers TaskMind supports: " + ", ".join(
            f"{n} ({p['label']}, {p['cost']})" for n, p in config.PROVIDERS.items()),
        f"Current folder: {os.getcwd()} ({'a git repo' if detect_repo() else 'not a git repo'})",
        "Voice on this computer: recorder " + ((voice.recorder_command("x.wav") or ["none"])[0])
        + ", speech-to-text " + ("ready" if voice.stt_provider(cfg) else "needs a Groq or OpenAI key")
        + " (don't suggest installing tools that are already here)",
    ]
    prof = insights.profile(cfg)
    if prof["name"] or prof["language"] != "auto":
        lines.append(f"About the user: name {prof['name'] or 'unknown'}; preferred language {prof['language']}"
                     + (" (but always answer in the language of the question if it differs)"
                        if prof["language"] != "auto" else "")
                     + (f"; works {prof['hours']}" if prof["hours"] else "")
                     + (f"; codes with {prof['coder']}" if prof["coder"] else ""))
    tasks = db.list_tasks()
    lines.append(f"Open tasks: {len(tasks)}")
    for t in tasks[:15]:
        steps = db.subtasks(t["id"])
        nxt = next((x for x in steps if not x["done"]), None)
        lines.append(f"- TM-{t['id']} {t['title']} | {t['status']} | due {t.get('due') or '-'} | "
                     f"steps {sum(x['done'] for x in steps)}/{len(steps)}"
                     + (f" | next: TM-{t['id']}.{nxt['position']} {nxt['title']}" if nxt else ""))
    return "\n".join(lines)


def command_reference(only=None) -> str:
    """Every command's usage line, straight from the parser, so the AI can't invent options."""
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    lines = []
    for name, sp in sub.choices.items():
        if only and name not in only:
            continue
        usage = " ".join(sp.format_usage().split()).replace("usage: ", "").replace(" [-h]", "")
        lines.append(f"{usage}  # {sp.description or ''}")
    lines.append("Times for --remind / tm remind set: +30m, +2h, +1d, 18:00, 9am, tomorrow 9am, tonight 9, "
                 "friday 10:30, YYYY-MM-DD HH:MM. Due dates (--due): YYYY-MM-DD, today, tomorrow, friday, +3d.")
    return "\n".join(lines)


def show_offline_help(question, reason=None):
    results = helpdocs.search(question, k=2)
    if reason:
        ui.out(f"[yellow]I couldn't ask the AI ({ui.escape(reason)}), so here's what the guide says.[/yellow]")
    if not results:
        ui.out("I didn't find that in the guide. Try `tm help` for all topics, or `tm tutorial`.")
        return
    for title, body in results:
        ui.heading(title)
        ui.markdown(helpdocs.excerpt(body))
    ui.out(f"\n[dim]More: tm help \"{ui.escape(results[0][0])}\"   ·   all topics: tm help[/dim]")


def cmd_ask(args):
    question = " ".join(args.question).strip()
    if not question:
        raise CLIError('Ask a question, e.g. tm ask "how do I set a reminder?"')
    db = open_db()
    cfg = config.load()
    cands = routing.candidates(cfg, "ask")
    if not cands:  # no working AI: the guide still answers
        show_offline_help(question, "no AI provider is set up yet; run `tm setup`")
        return
    guide = "\n\n".join(f"## {t}\n{b}" for t, b in helpdocs.search(question, k=2))
    try:
        with ui.working("Thinking..."):
            answer = AI("ask", cands=cands).run(
                lambda llm: stages.answer_question(llm, question, guide, current_state(db), command_reference()))
    except LLMError as exc:
        offline_fallback(question, exc, cfg)
        return
    ui.markdown(answer or "I don't have an answer for that. Try `tm help`.")


def offline_fallback(question, exc, cfg):
    """When the AI can't be reached: explain why (with key-fix steps) and answer from the guide."""
    lines = [l.strip() for l in str(exc).splitlines() if l.strip()]
    reason = lines[0]
    if reason.startswith("Every configured provider failed") and len(lines) > 1:
        # "Groq / model: The API key was rejected..." -> keep the cause, not the list header
        reason = "every provider failed; first: " + lines[1].split(": ", 1)[-1]
    reason = reason[:160]
    if "key" in str(exc).lower() and "rejected" in str(exc).lower():
        s = config.llm_settings(cfg)
        ui.out(f"[bold]Your {ui.escape(s['label'])} API key isn't working.[/bold] To fix it:\n"
               f"  1. Get a new key at {ui.escape(s['signup'])}\n"
               "  2. Run `tm setup`, choose your provider and paste the new key\n"
               "  3. Check it with `tm doctor`\n")
    show_offline_help(question, reason)


# `tm do`: what the brain may run, and which of those change or delete things.
DO_ALLOWED = {"add", "edit", "step", "done", "start", "remind", "calendar", "list", "show", "today", "go",
              "clarify", "analyze", "plan", "prompt", "sync", "verify", "import", "rm", "models", "doctor"}
DO_REMIND_ACTIONS = {"list", "set", "check"}  # not on/off: those change the crontab


def is_read_only(argv) -> bool:
    """Commands that only show things; a plan made only of these runs without asking."""
    if argv[0] in ("list", "show", "today", "models", "doctor"):
        return True
    return argv[:2] in (["step", "list"], ["remind", "list"], ["calendar", "link"]) or argv == ["remind"] \
        or (argv[0] == "prompt" and "--print" in argv)


def is_destructive(argv) -> bool:
    return argv[0] == "rm" or argv[:2] == ["step", "rm"]


def check_action(parser, argv) -> str:
    """Why a proposed command can't run, or "" when it's fine."""
    if argv[0] not in DO_ALLOWED:
        return f"`tm {argv[0]}` isn't something I can run for you"
    if argv[0] == "remind" and len(argv) > 1 and argv[1] not in DO_REMIND_ACTIONS:
        return f"`tm remind {argv[1]}` changes system settings; run it yourself"
    probe = [re.sub(r"^NEW(?=\.|$)", "1", a) for a in argv]
    try:
        parser.parse_args(probe)
    except UsageError as err:
        return friendly_usage_error(err, sorted(DO_ALLOWED), probe)
    except SystemExit:
        return "that command only shows help"
    return ""


def cmd_talk(args):
    """Hands-free: speak a request, TaskMind does it (after you agree) and answers out loud. Repeat."""
    if not interactive():
        raise CLIError("`tm talk` needs an interactive terminal.")
    speaker = make_speaker()
    ui.out("[bold]Talk to TaskMind.[/bold] Each turn: press Enter, speak, press Enter. Say [bold]stop[/bold] "
           "(or press Ctrl+C) to finish.\nTry: \"what should I work on today\", \"add a task to call the "
           "bank tomorrow\", \"mark task 1 done\".")
    speaker.say("I'm listening. What would you like to do?")
    turns = 0
    while True:
        typed = ui.ask("\n[Press Enter to speak, or type a request]")
        if typed.lower() in ("stop", "exit", "quit", "q"):
            break
        try:
            text = typed or listen()
        except voice.VoiceError as exc:
            ui.out(f"[yellow]{ui.escape(str(exc))}[/yellow]")
            continue
        if re.fullmatch(r"(stop|exit|quit|bye|goodbye|that's all|thats all|bas|band karo|ruko|khatam)",
                        text.lower().strip()):
            break
        turns += 1
        do_request(text, speaker=speaker)
    speaker.say("Okay, bye!" if turns else "Okay.")
    return 0


class _DoArgs:
    yes = False
    dry_run = False


def cmd_do(args):
    request = " ".join(args.request).strip()
    if args.voice:
        ui.out('Say what you want, e.g. "remind me about the CSV task tomorrow at 9".')
        request = listen()
    if not request:
        raise CLIError('Tell me what to do, e.g. tm do "remind me about task 1 tomorrow at 9"')
    return do_request(request, args)


def do_request(request, args=None, speaker=None):
    """Understand a request, show the commands, run them after the user agrees. Used by
    `tm do`, free text after `tm`, and `tm talk` (which also speaks the outcome)."""
    args = args or _DoArgs()
    db = open_db()
    cfg = config.load()
    cands = routing.candidates(cfg, "do")
    if not cands:
        offline_fallback(request, LLMError("no AI provider is set up yet; run `tm setup`"), cfg)
        return 1
    parser = build_parser()
    reference = command_reference(only=DO_ALLOWED)
    context = request
    for attempt in range(3):
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        try:
            with ui.working("Understanding..."):
                result = AI("do", cands=cands).run(
                    lambda llm: stages.brain(llm, context, reference, current_state(db), now))
        except LLMError as exc:
            ui.out("[yellow]I need a working AI to understand requests in your own words; the normal "
                   "commands still work (type `tm` to see them).[/yellow]")
            offline_fallback(request, exc, cfg)
            return 1
        kind, message = result["type"], result["message"]
        if speaker and message and kind != "actions":
            speaker.say(message)
        if kind == "question" and attempt < 2:
            ui.plain(message)
            reply = ui.ask(">") if interactive() else ""
            if not reply:
                return 0
            context += f"\n(You asked: {message} The user answered: {reply})"
            continue
        if kind == "unsupported":
            ui.plain(message or "TaskMind can't do that yet.")
            add_to_wishlist(request, message)
            ui.out("[dim]I've added this to your wishlist of things TaskMind can't do yet (`tm wishlist`).[/dim]")
            return 0
        if kind != "actions" or not result["actions"]:
            ui.markdown(message or "I'm not sure what to do. Try rephrasing, or `tm help`.")
            return 0
        problems = [(a, check_action(parser, a["argv"])) for a in result["actions"]]
        problems = [(a, p) for a, p in problems if p]
        if problems and attempt < 2:  # let the model correct itself once
            context = request + "\n(Your previous commands were rejected: " + "; ".join(
                f"tm {' '.join(a['argv'])}: {p}" for a, p in problems) + ". Use only the reference.)"
            continue
        if problems:
            ui.error("I couldn't turn that into valid commands: " + "; ".join(p for _, p in problems))
            return 1
        return run_actions(db, result, args, speaker)
    return 0


def spoken_outcome(db, argv) -> str:
    """What to say after a viewing command, so hands-free use doesn't need the screen."""
    tasks = db.list_tasks()
    if argv[0] == "list":
        if not tasks:
            return "Your list is empty."
        names = ", ".join(f"task {t['id']}, {t['title']}" for t in tasks[:5])
        return f"You have {len(tasks)} open task{'s' if len(tasks) != 1 else ''}: {names}."
    if argv[0] == "today":
        today = date.today().isoformat()
        due = [t for t in tasks if t.get("due") and t["due"] <= today]
        active = [t for t in tasks if t["status"] == "in_progress"]
        if not due and not active:
            return f"Nothing is due today. You have {len(tasks)} open tasks."
        parts = [f"{len(due)} due or overdue" if due else "", f"{len(active)} in progress" if active else ""]
        first = (due or active)[0]
        return f"Today: {' and '.join(p for p in parts if p)}. Start with task {first['id']}, {first['title']}."
    if argv[0] == "show" and len(argv) > 1 and REF_RE.match(argv[1]):
        task = db.get_task(parse_ref(argv[1])[0])
        if task:
            steps = db.subtasks(task["id"])
            nxt = next((s for s in steps if not s["done"]), None)
            return (f"Task {task['id']}, {task['title']}: {sum(s['done'] for s in steps)} of {len(steps)} steps done."
                    + (f" Next: {nxt['title']}." if nxt else ""))
    return ""


def run_actions(db, result, args, speaker=None):
    actions = result["actions"]
    if speaker and result["message"]:
        speaker.say(result["message"])
    if result["message"]:
        ui.plain(result["message"])
    ui.out("\n[bold]I'll run:[/bold]")
    for i, a in enumerate(actions, start=1):
        warn = "  [red]⚠ deletes[/red]" if is_destructive(a["argv"]) else ""
        why = f"  [dim]{ui.escape(a['why'])}[/dim]" if a["why"] else ""
        ui.out(f"  {i}. [bold cyan]tm {ui.escape(' '.join(shlex.quote(x) for x in a['argv']))}[/bold cyan]{warn}{why}")
    if args.dry_run:
        ui.out("[dim](dry run: nothing was changed)[/dim]")
        return 0
    destructive = any(is_destructive(a["argv"]) for a in actions)
    read_only = all(is_read_only(a["argv"]) for a in actions)
    if not args.yes and not read_only and not ui.confirm("Go ahead?", default=not destructive):
        ui.out("Nothing was changed.")
        return 0
    new_id = None
    for i, a in enumerate(actions, start=1):
        argv = list(a["argv"])
        if any(re.match(r"^NEW(\.|$)", x) for x in argv):
            if new_id is None:
                ui.error(f"Step {i} refers to a new task, but none was added before it. Stopped.")
                return 1
            argv = [re.sub(r"^NEW(?=\.|$)", str(new_id), x) for x in argv]
        if is_destructive(argv) and "-y" not in argv:
            argv.append("-y")  # you already confirmed above
        ui.out(f"\n[dim]$ tm {ui.escape(' '.join(shlex.quote(x) for x in argv))}[/dim]")
        code = main(argv)
        if code:
            ui.out(f"[yellow]Stopped at step {i} because it didn't succeed; the steps before it are done.[/yellow]")
            if speaker:
                speaker.say(f"That didn't work at step {i}. The details are on screen.")
            return code
        if argv[0] == "add":
            new_id = db.conn.execute("SELECT MAX(id) FROM tasks").fetchone()[0]
        if speaker and is_read_only(argv):
            said = spoken_outcome(db, argv)
            if said:
                speaker.say(said)
    if not read_only:
        ui.out(f"\n[green]Done[/green] ({len(actions)} command{'s' if len(actions) != 1 else ''}).")
        if speaker:
            speaker.say("Done.")
    return 0


def cmd_help(args):
    topic = " ".join(args.topic).strip()
    if not topic:
        ui.heading("Help topics")
        for title, _ in helpdocs.sections():
            ui.plain(f"  • {title}")
        ui.out('\n[dim]Read one: tm help reminders   ·   ask anything: tm ask "how do I ...?"   ·   '
               "guided start: tm tutorial[/dim]")
        return
    found = helpdocs.find_topic(topic)
    if not found:
        ui.out(f"No help topic matches '{ui.escape(topic)}'. See all topics with `tm help`, "
               f'or ask: tm ask "{ui.escape(topic)}"')
        return
    ui.heading(found[0])
    ui.markdown(found[1])


def cmd_tutorial(args):
    if not interactive():
        raise CLIError("The tutorial needs an interactive terminal.")
    if not routing.candidates(ensure_setup(), "clarify"):
        raise CLIError("Set up an AI provider first with `tm setup`.")
    return tutorial.run(main)


def cmd_setup(args):
    run_setup()


def cmd_models(args):
    cfg = config.load()
    s = config.llm_settings(cfg, args.provider)
    if s["needs_key"] and not s["api_key"]:
        raise CLIError(f"{s['label']} needs an API key first. Run `tm setup`.")
    with ui.working(f"Fetching {s['label']} models..."):
        ids = llm_client.from_settings({**s, "model": s["model"] or "-"}).list_models()
    shown = ids if args.all else chat_models(ids)
    for m in shown:
        ui.plain(f"  {m}{'  ← current' if m == s['model'] else ''}")
    hidden = len(ids) - len(shown)
    ui.out(f"\n[dim]{len(shown)} models" + (f" ({hidden} non-chat hidden, --all to show)" if hidden else "")
           + f". Switch with `tm config --provider {s['provider']} --model <id>`.[/dim]")


def cmd_config(args):
    cfg = config.load()
    changed = False
    if args.provider:
        if args.provider not in config.PROVIDERS:
            raise CLIError(f"Provider must be one of: {', '.join(config.PROVIDERS)}")
        cfg["provider"], cfg["base_url"], changed = args.provider, None, True
    provider = cfg["provider"]
    if args.model:
        cfg["models"][provider], changed = args.model, True
    if args.key:
        cfg["keys"][provider], changed = args.key, True
    if args.fallback is not None:
        names = [n.strip() for n in args.fallback.split(",") if n.strip()]
        for name in names:
            routing.parse_target(name)  # validates
        cfg["fallback"], changed = names, True
    for item in args.stage or []:
        stage, sep, target = item.partition("=")
        if not sep or stage not in routing.STAGES:
            raise CLIError(f"Use --stage <stage>=<provider[:model]>; stages: {', '.join(routing.STAGES)}")
        if target:
            routing.parse_target(target)
            cfg["stages"][stage] = target
        else:
            cfg["stages"].pop(stage, None)
        changed = True
    if args.coder is not None:
        if args.coder not in ("", "none") and args.coder not in config.PROVIDERS:
            raise CLIError(f"--coder must be one of: {', '.join(config.PROVIDERS)} (or none)")
        cfg["coder"], changed = (args.coder if args.coder not in ("", "none") else None), True
    if args.mode is not None:
        cfg["mode"], changed = args.mode, True
    if args.remind_time is not None:
        if not reminders.parse_time_of_day(args.remind_time):
            raise CLIError("--remind-time takes a time like 09:00 or 8:30am.")
        hm = reminders.parse_time_of_day(args.remind_time)
        cfg.setdefault("reminders", {})["time"], changed = f"{hm[0]:02d}:{hm[1]:02d}", True
    if args.voice_output is not None:
        cfg.setdefault("voice", {})["output"], changed = args.voice_output, True
    if args.voice_name is not None:
        cfg.setdefault("voice", {})["name"], changed = args.voice_name, True
    if args.review is not None:
        if args.review not in ("auto", "off"):
            routing.parse_target(args.review)
        cfg["review"], changed = args.review, True
    if changed:
        config.save(cfg)
        ui.out("[green]Saved.[/green]")
    show_settings(cfg)
    if not changed:
        ui.out("[dim]Run `tm setup` for guided setup, or `tm models` to see available models.[/dim]")


def cmd_doctor(args):
    cfg = ensure_setup()
    targets = routing.candidates(cfg, "clarify") if args.all else [config.llm_settings(cfg)]
    failed = 0
    for s in targets:
        try:
            with ui.working(f"Calling {routing.describe(s)}..."):
                reply = test_connection(llm_client.from_settings(s))
            ui.out(f"[green]OK[/green]   {ui.escape(routing.describe(s))}: {ui.escape(reply)}")
        except LLMError as exc:
            failed += 1
            ui.out(f"[red]FAIL[/red] {ui.escape(routing.describe(s))}: {ui.escape(str(exc).splitlines()[0])}")
    return 1 if failed else 0


def cmd_add(args):
    db = open_db()
    if args.repo:
        repo = resolve_repo(args.repo)
    else:
        repo = None if args.no_repo or simple_mode() else detect_repo()
    fields = {"title": args.title, "description": args.description or "", "due": parse_due(args.due),
              "priority": args.priority or "medium",
              "remind_at": reminders.parse_when(args.remind) if args.remind else None}
    if args.voice:
        ui.out("Say the task, e.g. \"add rate limiting to login, high priority, due Friday\".")
        transcript = listen()  # once, outside the failover retry
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        spoken = AI("clarify").run(lambda llm: stages.parse_spoken_task(llm, transcript, now))
        # Options typed on the command line win over what was said.
        fields.update({k: v for k, v in spoken.items()
                       if not (k == "title" and args.title) and not (k == "description" and args.description)
                       and not (k == "due" and args.due) and not (k == "priority" and args.priority)
                       and not (k == "remind_at" and args.remind)})
        ui.table(["Field", "Value"], [[k.replace("_at", ""), fields[k] or "-"]
                                      for k in ("title", "description", "due", "priority", "remind_at")])
        if not ui.confirm("Save this task?", default=True):
            ui.out("Not saved.")
            return
    elif not args.title:
        raise CLIError('Give a title (tm add "...") or use --voice.')
    task_id = db.add_task(fields["title"], fields["description"], repo=repo, due=fields["due"],
                          priority=fields["priority"])
    if fields["remind_at"]:
        db.update_task(task_id, remind_at=fields["remind_at"])
        ui.out(f"🔔 Reminder set for {fields['remind_at']}" + ("" if reminders.schedule_enabled() else
               " [dim](turn on notifications with `tm remind on`)[/dim]"))
    ui.out(f"Added [bold]TM-{task_id}[/bold]" + (f" (repo: {repo})" if repo else ""))
    ui.out(f"[dim]Next: `tm go {task_id}`. I'll ask a few questions, "
           + ("read the code" if repo else "think it through") + ", and make a step-by-step plan.[/dim]")


def cmd_import(args):
    db = open_db()
    repo = resolve_repo(args.repo) if args.repo else detect_repo()
    with ui.working(f"Fetching issues from {args.owner_repo}..."):
        issues = github.fetch_issues(args.owner_repo, labels=args.label,
                                     assignee=args.assignee, limit=args.limit)
    added = 0
    for issue in issues:
        ref = f"{args.owner_repo}#{issue['number']}"
        if db.find_by_source("github", ref):
            continue
        body = (issue.get("body") or "").strip()
        db.add_task(f"{issue['title']} (#{issue['number']})",
                    f"{body}\n\nGitHub: {issue['html_url']}".strip(),
                    repo=repo, source="github", source_ref=ref)
        added += 1
    ui.out(f"Imported {added} new issue(s), skipped {len(issues) - added} already imported.")


def cmd_list(args):
    db = open_db()
    tasks = db.list_tasks(status=args.status, include_done=args.all)
    if not tasks:
        ui.out("No tasks. Add one with `tm add \"...\"`.")
        return
    today = date.today().isoformat()
    rows = []
    for t in tasks:
        due = t.get("due") or ""
        if due and due < today and t["status"] != "done":
            due += " ⚠️"
        rows.append([f"TM-{t['id']}", status_label(t["status"]), t["priority"],
                     due, progress(db, t["id"]), t["title"]])
    ui.table(["ID", "Status", "Priority", "Due", "Done", "Title"], rows)


def cmd_show(args):
    db = open_db()
    task = get_task(db, args.id)
    ui.heading(f"TM-{task['id']}  {task['title']}")
    meta = [status_label(task["status"]), f"priority: {task['priority']}"]
    if task.get("due"):
        meta.append(f"due: {task['due']}")
    if task.get("remind_at"):
        meta.append(f"🔔 {task['remind_at']}")
    if task.get("repo"):
        meta.append(f"repo: {task['repo']}")
    if task["source"] != "manual":
        meta.append(f"source: {task['source_ref']}")
    ui.plain("  " + " | ".join(meta))
    if task.get("definition"):
        ui.heading("Definition")
        ui.markdown(task["definition"])
    elif task.get("description"):
        ui.heading("Description")
        ui.plain(task["description"])
    ui.bullets("Acceptance criteria", task.get("acceptance"))
    if task.get("analysis"):
        show_analysis(task["analysis"])
    show_plan(db, task)
    if task.get("verification"):
        show_verification(task["verification"], full=False)
    if task.get("ai_log"):
        ui.plain("  AI used: " + ", ".join(f"{stage} → {v['provider']}/{v['model']}"
                                         for stage, v in task["ai_log"].items()))
    events = db.events(task["id"], limit=8)
    if events:
        ui.heading("Activity")
        for e in events:
            ui.plain(f"  {e['at'][:16].replace('T', ' ')}  {e['message']}")


def cmd_edit(args):
    db = open_db()
    task = get_task(db, args.id)
    fields = {}
    if args.title:
        fields["title"] = args.title
    if args.description is not None:
        fields["description"] = args.description
    if args.due is not None:
        fields["due"] = parse_due(args.due) if args.due else None
    if args.priority:
        fields["priority"] = args.priority
    if args.repo is not None:
        fields["repo"] = resolve_repo(args.repo) if args.repo else None
    if args.status:
        fields["status"] = args.status
    if args.remind is not None:
        fields["remind_at"] = reminders.parse_when(args.remind) if args.remind else None
    if not fields:
        raise CLIError("Nothing to change. See `tm edit --help`.")
    db.update_task(task["id"], **fields)
    db.log(task["id"], "edited", "Changed " + ", ".join(fields))
    ui.out(f"Updated TM-{task['id']}.")


def cmd_clarify(args):
    db = open_db()
    run_clarify(db, get_task(db, args.id), ask_questions=not args.no_questions, use_voice=args.voice)


def cmd_analyze(args):
    db = open_db()
    run_analyze(db, get_task(db, args.id))


def cmd_plan(args):
    db = open_db()
    run_plan(db, get_task(db, args.id))


def cmd_go(args):
    db = open_db()
    ensure_setup()
    task = get_task(db, args.id)
    todo = [name for name, needed in (
        ("clarify", not task.get("definition") or args.redo),
        ("analyze", not task.get("analysis") or args.redo),
        ("plan", not db.subtasks(task["id"]) or args.redo)) if needed]
    if not todo:
        ui.out(f"TM-{task['id']} was already thought through. Showing the plan "
               f"([dim]`tm go {task['id']} --redo` starts over[/dim]).")
    for n, name in enumerate(todo, start=1):
        step = (n, len(todo))
        if name == "clarify":
            task = run_clarify(db, task, ask_questions=not args.no_questions, use_voice=args.voice, step=step)
        elif name == "analyze":
            task = run_analyze(db, task, step=step)
        else:
            task = run_plan(db, task, step=step)
    if "plan" not in todo:
        show_plan(db, task)
    show_go_summary(db, db.get_task(task["id"]))


def _build_prompt(db, args):
    task_id, pos = parse_ref(args.ref)
    task = get_task(db, task_id)
    subs = db.subtasks(task_id)
    if pos is not None and pos not in {s["position"] for s in subs}:
        raise CLIError(f"TM-{task_id} has no subtask {pos}.")
    return task, stages.handoff_prompt(task, subs, pos)


def cmd_prompt(args):
    db = open_db()
    task, text = _build_prompt(db, args)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        ui.out(f"Wrote {args.out}")
    elif args.print or not copy_to_clipboard(text):
        ui.plain(text)
    else:
        ui.out(f"[green]Copied the TM-{task['id']} prompt to your clipboard.[/green] "
               "Paste it into Claude Code or Cursor.")


TOOLS = {"claude": ["claude"], "cursor": ["cursor-agent"], "codex": ["codex"]}


def cmd_exec(args):
    db = open_db()
    task, text = _build_prompt(db, args)
    binary = TOOLS[args.tool][0]
    if not shutil.which(binary):
        raise CLIError(f"`{binary}` is not installed. Use `tm prompt {args.ref}` and paste it instead.")
    cwd = task.get("repo") or "."
    ui.out(f"This will start {args.tool} in {cwd} with the TM-{task['id']} prompt. "
           "It can edit files there, and it will ask for its own approvals.")
    if not args.yes and not ui.confirm("Continue?"):
        return
    if task["status"] != "done":
        db.update_task(task["id"], status=_advance(task, "in_progress"))
    db.log(task["id"], "exec", f"Handed to {args.tool}")
    subprocess.run([*TOOLS[args.tool], text], cwd=cwd)
    ui.out(f"[dim]When you've committed, run `tm sync {task['id']}` to update progress.[/dim]")


def _build_goal(db, args):
    """(task text, short title, project folder, existing task or None) for `tm build`."""
    words = list(args.task or [])
    if len(words) == 1 and words[0].isdigit():
        task = get_task(db, int(words[0]))
        parts = [task["title"], task.get("description") or "", task.get("definition") or ""]
        steps = [s for s in db.subtasks(task["id"]) if not s.get("done")]
        if steps:
            parts.append("Plan:\n" + "\n".join(f"- {s['title']}: {s.get('details') or ''}" for s in steps))
        text = "\n\n".join(p for p in parts if p)
        return text, task["title"], task.get("repo") or os.getcwd(), task
    if args.voice:
        ui.out("Say what you want built.")
        words = [listen()]
        ui.out(f"[dim]Heard: {ui.escape(words[0])}[/dim]")
    text = " ".join(words).strip()
    if not text:
        raise CLIError('Say what to build: tm build "add a dark mode toggle" (or tm build 3 for task 3).')
    return text, text.splitlines()[0][:100], os.getcwd(), None


def _new_project(name):
    folder = Path(name).expanduser().resolve()
    if folder.exists() and any(folder.iterdir()):
        raise CLIError(f"{folder} already exists and isn't empty. Pick another name, or cd into it and "
                       "run `tm build` without --new.")
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _show_build_event(kind, name=None, args=None, result=None, command=None, code=None, output=None):
    args = args or {}
    if kind == "tool":
        if name == "run_command":
            ui.out(f"  ▶ [cyan]{ui.escape(str(args.get('command', ''))[:120])}[/cyan]")
        elif name in ("write_file", "edit_file"):
            ui.out(f"  ✏️  {ui.escape(str(args.get('path') or args.get('file') or ''))}")
        else:
            target = args.get("path") or args.get("pattern") or args.get("query") or "."
            ui.out(f"  [dim]🔍 {name.replace('_', ' ')} {ui.escape(str(target))[:80]}[/dim]")
    elif kind == "result":
        if name == "run_command":
            first = result.split("\n", 1)[0]
            ok = first == "exit code 0"
            ui.out(f"    [{'green' if ok else 'yellow'}]{ui.escape(first)}[/]" if first.startswith("exit code")
                   else f"    [yellow]{ui.escape(first[:160])}[/yellow]")
        elif result.startswith("Tool error"):
            ui.out(f"    [yellow]{ui.escape(result[:160])}[/yellow]")
    elif kind == "check":
        ui.out(f"\n[bold]Checking it myself:[/bold] {ui.escape(command)}")
    elif kind == "checked":
        if code == 0:
            ui.out("[green]✅ Passed.[/green]")
        else:
            ui.out(f"[yellow]❌ Failed (exit code {code}). Sending the errors back to fix them...[/yellow]")


BUILD_RESULT = {
    "passed": "[bold green]✅ Done and checked: the tests pass.[/bold green]",
    "failed": "[bold red]❌ Not done: the tests still fail after several fixes.[/bold red]",
    "unverified": "[bold yellow]🟡 Built, but not proven: there's no test to run.[/bold yellow]",
    "unfinished": "[bold yellow]🟡 Not finished: the AI ran out of steps.[/bold yellow]",
}


def cmd_build(args):
    from taskmind import builder

    cfg = ensure_setup()
    db = open_db()
    text, title, root, task = _build_goal(db, args)
    if args.new:
        root = _new_project(args.new)
    try:
        base, branch = builder.start_branch(root, title, new_project=bool(args.new))
    except builder.GitError as exc:
        raise CLIError(str(exc)) from exc
    ws = builder.Workspace(root, approve=lambda command, reason: args.yes or (
        interactive() and ui.confirm(f"The AI wants to run [bold]{ui.escape(command)}[/bold] "
                                     f"(it {reason}). Allow?")))
    cands = routing.candidates(cfg, "build")
    ensure_code_consent(ws.repo, "build", cands)
    if task is None:
        task = get_task(db, db.add_task(title, text if text != title else "", repo=str(ws.root)))
    db.update_task(task["id"], status=_advance(task, "in_progress"))
    db.log(task["id"], "build", f"Build started on branch {branch}")
    ui.out(f"🔨 Building [bold]TM-{task['id']}[/bold] in {ui.escape(str(ws.root))}")
    ui.out(f"[dim]On a new branch, {ui.escape(branch)}: your {ui.escape(base)} branch isn't touched "
           "until you keep the result.[/dim]\n")

    def leave_note():
        ui.out(f"[dim]The work so far is on branch {ui.escape(branch)}. Back to yours: "
               f"git checkout {ui.escape(base)}[/dim]")

    profile = insights.profile(cfg)
    try:
        report = AI("build", task, cands).run(lambda llm: builder.build(
            llm, text, ws, check=args.check or "", max_steps=args.max_steps, language=profile["language"],
            simple=simple_mode() or args.simple, on_event=_show_build_event))
    except (LLMError, KeyboardInterrupt):
        builder.save_work(ws.root, f"TaskMind (unfinished): {title}")
        leave_note()
        raise
    files = builder.save_work(ws.root, f"{title}\n\n{report['summary']}\n\nTM-{task['id']}".strip())

    ui.out("\n" + BUILD_RESULT[report["status"]])
    if report["summary"]:
        ui.out(ui.escape(report["summary"]))  # not markdown: it would swallow <placeholders>
    if files:
        ui.out(f"[dim]Changed: {ui.escape(', '.join(files[:12]))}{' ...' if len(files) > 12 else ''}[/dim]")
    if report["status"] == "failed":
        ui.out(f"[dim]Last test output:[/dim]\n{ui.escape(report['check_output'][-1200:])}")
    if report["changed_tests"]:
        ui.out(f"[yellow]Look at these before keeping: the AI changed tests that already existed: "
               f"{ui.escape(', '.join(report['changed_tests']))}[/yellow]")
    db.log(task["id"], "build", f"{report['status']}: {report['check'] or 'no check'}; "
                                f"{len(files)} files; {report['steps']} steps")
    if not files:
        builder.discard(ws.root, base, branch)
        ui.out("Nothing was changed, so there's nothing to keep.")
        return 1

    passed = report["status"] == "passed"
    if args.yes and passed or interactive() and ui.confirm(
            f"Keep it (add it to your {base} branch)?", default=passed):
        builder.keep(ws.root, base, branch)
        if passed:
            for step in db.subtasks(task["id"]):
                db.mark_subtask(task["id"], step["position"], True, ref=f"tm build ({branch})")
            db.update_task(task["id"], status="done")
        ui.out(f"[green]Kept on {ui.escape(base)}.[/green] " + ("" if passed else
               f"[dim]TM-{task['id']} stays open until it's proven.[/dim]"))
        return 0 if passed else 1
    if interactive() and ui.confirm("Throw the work away?", default=False):
        builder.discard(ws.root, base, branch)
        ui.out("Thrown away. Your code is exactly as it was.")
        return 1
    ui.out(f"Left on branch [bold]{ui.escape(branch)}[/bold] for you to look at "
           f"(git diff {ui.escape(base)}...{ui.escape(branch)}).")
    leave_note()
    return 0 if passed else 1


def cmd_step(args):
    db = open_db()
    if args.action == "add":
        task = get_task(db, parse_ref(args.ref)[0])
        if not args.text:
            raise CLIError(f'Give the step a title: tm step add {task["id"]} "Write the tests"')
        pos = db.add_subtask(task["id"], args.text, args.details or "")
        if task["status"] == "done":
            db.update_task(task["id"], status="in_progress")  # new work reopens a finished task
        elif STATUSES.index(task["status"]) < STATUSES.index("planned"):
            db.update_task(task["id"], status="planned")
        db.log(task["id"], "plan", f"Step TM-{task['id']}.{pos} added: {args.text}")
        ui.out(f"Added step [bold]TM-{task['id']}.{pos}[/bold]: {ui.escape(args.text)}")
    elif args.action in ("edit", "rm"):
        task_id, pos = parse_ref(args.ref)
        task = get_task(db, task_id)
        if pos is None:
            raise CLIError(f"Say which step, e.g. tm step {args.action} {task_id}.2")
        if pos not in {s["position"] for s in db.subtasks(task_id)}:
            raise CLIError(f"TM-{task_id} has no step {pos}. See `tm step list {task_id}`.")
        if args.action == "edit":
            if not args.text and args.details is None:
                raise CLIError(f'Give a new title or details: tm step edit {task_id}.{pos} "New title" -d "..."')
            db.update_subtask(task_id, pos, title=args.text, details=args.details)
            db.log(task_id, "plan", f"Step TM-{task_id}.{pos} edited")
            ui.out(f"Updated step TM-{task_id}.{pos}.")
        else:
            step = next(s for s in db.subtasks(task_id) if s["position"] == pos)
            if not args.yes and not ui.confirm(f"Remove step TM-{task_id}.{pos} '{step['title']}'?"):
                return
            db.remove_subtask(task_id, pos)
            db.log(task_id, "plan", f"Step TM-{task_id}.{pos} removed: {step['title']}")
            ui.out(f"Removed step TM-{task_id}.{pos}. [dim](Step numbers aren't reused, so commits "
                   f"mentioning other steps stay correct.)[/dim]")
            if tracking.finish_if_complete(db, task_id):
                ui.out(f"🎉 All remaining steps are done, TM-{task_id} is complete.")
        task = db.get_task(task_id)
    elif args.action == "show":
        task_id, pos = parse_ref(args.ref)
        task = get_task(db, task_id)
        if pos is not None:
            step = next((s for s in db.subtasks(task_id) if s["position"] == pos), None)
            if not step:
                raise CLIError(f"TM-{task_id} has no step {pos}. See `tm step list {task_id}`.")
            ui.heading(f"TM-{task_id}.{pos}  {step['title']}")
            ui.plain(f"  {'✅ done' if step['done'] else '⬜ not done yet'}"
                     + (f"  ·  ~{step['estimate_hours']}h" if step.get("estimate_hours") else "")
                     + f"  ·  part of TM-{task_id} {task['title']}")
            if step.get("details"):
                ui.plain(f"\n{step['details']}")
            if step.get("files"):
                ui.bullets("Files", step["files"])
            nxt = (f"tm done {task_id}.{pos} --undo" if step["done"] else f"tm done {task_id}.{pos}")
            ui.out(f"\n[dim]{'Reopen' if step['done'] else 'Mark it done'}: {nxt}   ·   "
                   f"instructions for a coding agent: tm prompt {task_id}.{pos}[/dim]")
            return
    else:  # list
        task = get_task(db, parse_ref(args.ref)[0])
    show_plan(db, db.get_task(task["id"]))
    if not db.subtasks(task["id"]):
        ui.out(f'[dim]No steps yet. Add your own with tm step add {task["id"]} "..." '
               f'or let the AI plan them with tm go {task["id"]}.[/dim]')


def cmd_start(args):
    db = open_db()
    task = get_task(db, args.id)
    db.update_task(task["id"], status="in_progress")
    db.log(task["id"], "status", "Started")
    ui.out(f"TM-{task['id']} is in progress.")


def cmd_done(args):
    db = open_db()
    task_id, pos = parse_ref(args.ref)
    task = get_task(db, task_id)
    if pos is None:
        db.update_task(task_id, status="in_progress" if args.undo else "done")
        db.log(task_id, "status", "Reopened" if args.undo else "Marked done")
        ui.out(f"TM-{task_id} {'reopened' if args.undo else 'done ✅'}")
        return
    if pos not in {s["position"] for s in db.subtasks(task_id)}:
        raise CLIError(f"TM-{task_id} has no step {pos}. See `tm show {task_id}`.")
    if not db.mark_subtask(task_id, pos, not args.undo, "manual"):
        ui.out(f"TM-{task_id}.{pos} was already {'open' if args.undo else 'done'} (or does not exist).")
        return
    db.log(task_id, "progress", f"TM-{task_id}.{pos} {'reopened' if args.undo else 'done'}")
    if args.undo and task["status"] == "done":
        db.update_task(task_id, status="in_progress")
    elif not args.undo and task["status"] not in ("in_progress", "done"):
        db.update_task(task_id, status="in_progress")
    ui.out(f"TM-{task_id}.{pos} {'reopened' if args.undo else 'done ✅'}  ({progress(db, task_id)})")
    if not args.undo and tracking.finish_if_complete(db, task_id):
        ui.out(f"🎉 All subtasks done, TM-{task_id} is complete.")
        if task.get("repo"):
            ui.out(f"[dim]Run `tm verify {task_id}` to check the code against the plan.[/dim]")


def cmd_sync(args):
    db = open_db()
    ai = AI("sync") if args.ai else None
    matcher = (lambda _ai, *rest: ai.run(lambda llm: stages.match_commits(llm, *rest))) if ai else None
    tasks = [get_task(db, args.id)] if args.id else db.list_tasks()
    total = 0
    for task in tasks:
        if not task.get("repo") and task["source"] != "github":
            continue
        try:
            with ui.working(f"Syncing TM-{task['id']}..."):
                changes = tracking.sync_task(db, task, ai, matcher)
        except (ValueError, RuntimeError, github.GitHubError) as exc:
            # One broken task (e.g. its repo folder was moved) must not stop the others.
            ui.out(f"[yellow]TM-{task['id']}: {ui.escape(str(exc))}[/yellow] "
                   f"[dim](fix with `tm edit {task['id']} --repo <path>`)[/dim]")
            continue
        for change in changes:
            ui.plain(f"  {change}")
        total += len(changes)
    ui.out("Everything is up to date." if not total else f"{total} update(s).")


def cmd_verify(args):
    db = open_db()
    task = get_task(db, args.id)
    repo = task_repo(task)
    if not repo:
        raise CLIError(f"TM-{task['id']} is not linked to a repo. Link one with `tm edit {task['id']} --repo <path>`.")
    try:
        changes = verify.collect_changes(repo, task["id"], args.base, since=task["created_at"])
        planned_by = (task.get("ai_log") or {}).get("analyze", {}).get("provider")
        cands, note = routing.review_candidates(ensure_setup(), planned_by)
        ensure_code_consent(repo, "verify", cands)
        ai = AI("verify", task, cands)
        with ui.working(f"Checking {changes['source']} against the plan..."):
            result = ai.run(lambda llm: verify.run(llm, task, db.subtasks(task["id"]), changes))
    except verify.NothingToVerify as exc:
        raise CLIError(str(exc)) from exc
    result["reviewed_by"] = routing.describe(ai.used)
    result["review_note"] = note
    db.update_task(task["id"], verification=result)
    c = result["counts"]
    db.log(task["id"], "verified", f"Verified: {c['done']} done, {c['partial']} partial, "
                                   f"{c['missing']} missing, {c['unclear']} unclear")
    show_verification(result)
    gaps = c["missing"] + c["partial"]
    if gaps:
        ui.out(f"\n[dim]Fix the gaps (try `tm prompt {task['id']}` with the list above), "
               f"then run `tm verify {task['id']}` again.[/dim]")
    # --strict only passes when everything was actually checked and found done.
    unchecked = c["unclear"] + len(result.get("omitted") or [])
    if args.strict and not gaps and unchecked:
        ui.out(f"[yellow]--strict: {unchecked} item(s) could not be checked, so this does not pass.[/yellow]")
    return 1 if args.strict and (gaps or unchecked) else 0


def cmd_today(args):
    db = open_db()
    today = date.today().isoformat()
    tasks = db.list_tasks()
    overdue = [t for t in tasks if t.get("due") and t["due"] < today]
    due_today = [t for t in tasks if t.get("due") == today]
    active = [t for t in tasks if t["status"] == "in_progress"]
    ready = [t for t in tasks if t["status"] == "planned"]
    unshaped = [t for t in tasks if t["status"] in ("inbox", "clarified", "analyzed")]
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    finished = [e for e in db.events(since=since, limit=200) if e["kind"] == "done"]

    def next_step(t):
        nxt = next((s for s in db.subtasks(t["id"]) if not s["done"]), None)
        return f" → next: TM-{t['id']}.{nxt['position']} {nxt['title']}" if nxt else ""

    sections = [
        ("⚠️  Overdue", [f"TM-{t['id']} {t['title']} (due {t['due']})" for t in overdue]),
        ("📅 Due today", [f"TM-{t['id']} {t['title']}" for t in due_today]),
        ("🚧 In progress", [f"TM-{t['id']} {t['title']} [{progress(db, t['id'])}]{next_step(t)}" for t in active]),
        ("🗺️  Planned, ready to start", [f"TM-{t['id']} {t['title']} ({t['priority']})" for t in ready]),
        ("📥 Needs thinking (`tm go <id>`)", [f"TM-{t['id']} {t['title']} ({t['status']})" for t in unshaped]),
        ("✅ Finished in the last 24h", finished_titles(db, finished)),
    ]
    name = insights.profile(config.load())["name"]
    ui.heading(f"Today, {date.today().strftime('%A %d %b')}" + (f" · {name}" if name else ""))
    if not any(items for _, items in sections):
        ui.out("Nothing on your plate. Add a task with `tm add \"...\"`.")
        return
    for title, items in sections:
        ui.bullets(title, items)
    tips = insights.suggestions(db, config.load(), reminders.schedule_enabled())
    if tips:
        ui.heading("💡 Suggestions")
        for text, command in tips[:4]:
            ui.plain(f"  • {text}  →  {command}")
    briefing = None
    if args.ai:
        digest = "\n".join(f"{title}:\n" + "\n".join(items) for title, items in sections if items)
        with ui.working("Writing your briefing..."):
            briefing = AI("today").run(lambda llm: stages.briefing(llm, digest))
        ui.heading("Briefing")
        ui.plain(briefing)
    if args.speak:
        make_speaker().say(briefing or spoken_summary(sections))


def cmd_rm(args):
    db = open_db()
    task = get_task(db, args.id)
    if not args.yes and not ui.confirm(f"Delete TM-{task['id']} '{task['title']}'?"):
        return
    db.delete_task(task["id"])
    ui.out(f"Deleted TM-{task['id']}.")


# Argument parsing


def build_parser():
    p = FriendlyParser(
        prog="tm",
        description="TaskMind: say what you want built; it builds it, tests it and proves it works. "
                    "Start with: tm build \"...\"",
    )
    p.add_argument("--version", action="version", version=f"taskmind {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def add(name, func, help_text):
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.set_defaults(func=func)
        return sp

    add("tutorial", cmd_tutorial, "A guided 10-minute first task that explains every command")
    sp = add("ask", cmd_ask, "Ask anything about using TaskMind or your tasks (works offline too)")
    sp.add_argument("question", nargs="*")
    add("talk", cmd_talk, "Hands-free voice mode: speak requests, hear the answers")
    sp = add("do", cmd_do, "Say what you want in your own words; I propose the commands and run them after you agree")
    sp.add_argument("request", nargs="*")
    sp.add_argument("--voice", action="store_true", help="Say it instead of typing")
    sp.add_argument("-y", "--yes", action="store_true", help="Run without asking first")
    sp.add_argument("--dry-run", action="store_true", help="Only show what would run")
    sp = add("me", cmd_me, "Your profile: name, language, working hours, coding tool")
    sp.add_argument("--name")
    sp.add_argument("--language", choices=insights.LANGUAGES)
    sp.add_argument("--hours", help="Working hours, e.g. 09:00-18:00")
    sp.add_argument("--coder", help="Your AI coding tool, e.g. Claude Code")
    add("week", cmd_week, "Your week: what you finished, what's slipping")
    sp = add("greet", cmd_greet, "Your day in one line; --install greets you each time you log in (Linux)")
    sp.add_argument("--notify", action="store_true", help="As a desktop notification")
    sp.add_argument("--speak", action="store_true", help="Read it out loud")
    sp.add_argument("--install", action="store_true", help="Greet me automatically when I log in")
    sp.add_argument("--uninstall", action="store_true", help="Stop the login greeting")
    sp = add("report-bug", cmd_report_bug, "Save a bug report (version, system, recent errors; no keys or code)")
    sp.add_argument("what", nargs="*", help="What happened")
    sp.add_argument("-o", "--out", help="Where to save it")
    sp.add_argument("--open", action="store_true", help="Open the issue page, if one is set up")
    sp = add("feedback", cmd_feedback, "Tell us what would make TaskMind better")
    sp.add_argument("text", nargs="*")
    sp = add("backup", cmd_backup, "Back up your task list (also done automatically every day), or restore one")
    sp.add_argument("-o", "--out", help="Where to write the backup")
    sp.add_argument("--list", action="store_true", help="Show existing backups")
    sp.add_argument("--restore", metavar="FILE", help="Replace your task list with this backup")
    sp = add("export", cmd_export, "Export every task, step and activity as JSON (your data is yours)")
    sp.add_argument("-o", "--out", help="File to write (default: print)")
    add("wishlist", cmd_wishlist, "Things you asked `tm do` for that TaskMind can't do yet")
    sp = add("help", cmd_help, "Help topics from the guide, offline; `tm help <topic>`")
    sp.add_argument("topic", nargs="*")
    add("setup", cmd_setup, "Guided setup: choose an AI provider, add its key, pick a model")

    sp = add("voice", cmd_voice, "Check voice input/output; --test to try it")
    sp.add_argument("--test", action="store_true", help="Speak a test sentence and try the microphone")

    sp = add("models", cmd_models, "List the models your AI provider offers")
    sp.add_argument("--provider", choices=list(config.PROVIDERS), help="Another provider than the current one")
    sp.add_argument("--all", action="store_true", help="Include non-chat models")

    sp = add("config", cmd_config, "Show or change the AI provider, model and API key")
    sp.add_argument("--provider", help=", ".join(config.PROVIDERS))
    sp.add_argument("--model")
    sp.add_argument("--key", help="API key for the provider (stored in ~/.taskmind/config.json)")
    sp.add_argument("--fallback", metavar="P1,P2", help="Backup providers in order, e.g. groq,gemini "
                                                          "(empty string clears)")
    sp.add_argument("--stage", action="append", metavar="STAGE=PROVIDER[:MODEL]",
                    help=f"Use a specific AI for one stage ({', '.join(routing.STAGES)}); "
                         "repeatable; STAGE= clears it")
    sp.add_argument("--coder", metavar="PROVIDER", help="The provider whose coding tool you use, so "
                                                        "`tm verify` picks a different AI (none clears)")
    sp.add_argument("--mode", choices=["developer", "simple"],
                    help="simple: plain wording for everyday tasks, no git linking")
    sp.add_argument("--remind-time", metavar="HH:MM", help="Daily time for due-date and overdue "
                                                             "reminders (default 09:00)")
    sp.add_argument("--voice-output", choices=voice.OUTPUT_MODES,
                    help="How TaskMind speaks: auto (Groq voice, else this computer's voice), groq, local, off")
    sp.add_argument("--voice-name", choices=voice.TTS_VOICES, help="Groq voice (default autumn)")
    sp.add_argument("--review", metavar="auto|off|PROVIDER[:MODEL]",
                    help="Who runs `tm verify` (default auto: an AI other than your coder and planner)")

    sp = add("doctor", cmd_doctor, "Check that the AI provider works")
    sp.add_argument("--all", action="store_true", help="Check the main provider and every backup")

    sp = add("add", cmd_add, "Capture a new task (links the current git repo automatically)")
    sp.add_argument("title", nargs="?", help="Task title (optional with --voice)")
    sp.add_argument("--voice", action="store_true", help="Say the task instead of typing it")
    sp.add_argument("-d", "--description")
    sp.add_argument("--repo", help="Path to the code repo for this task")
    sp.add_argument("--no-repo", action="store_true", help="Do not link a repo (non-code task)")
    sp.add_argument("--due", help="YYYY-MM-DD, today, tomorrow or +Nd")
    sp.add_argument("-p", "--priority", choices=PRIORITIES, help="Default: medium")
    sp.add_argument("--remind", metavar="WHEN", help="Reminder: +30m, 18:00, 9am, tomorrow 9am, friday 10:30, "
                                                     "or YYYY-MM-DD HH:MM")

    sp = add("import", cmd_import, "Import open GitHub issues as tasks")
    sp.add_argument("owner_repo", metavar="owner/repo")
    sp.add_argument("--repo", help="Local checkout to link the tasks to")
    sp.add_argument("--label", help="Comma-separated labels")
    sp.add_argument("--assignee", help="GitHub username, or * / none")
    sp.add_argument("--limit", type=int, default=30)

    sp = add("list", cmd_list, "Show your task board")
    sp.add_argument("-a", "--all", action="store_true", help="Include done tasks")
    sp.add_argument("-s", "--status", choices=STATUSES)

    sp = add("show", cmd_show, "Show everything about one task")
    sp.add_argument("id", type=int)

    sp = add("edit", cmd_edit, "Change a task's details")
    sp.add_argument("id", type=int)
    sp.add_argument("--title")
    sp.add_argument("-d", "--description")
    sp.add_argument("--due", help="Empty string clears it")
    sp.add_argument("-p", "--priority", choices=PRIORITIES)
    sp.add_argument("--repo", help="Empty string unlinks it")
    sp.add_argument("--status", choices=STATUSES)
    sp.add_argument("--remind", metavar="WHEN", help="Set a reminder (see `tm add --help`); empty string clears it")

    sp = add("clarify", cmd_clarify, "Stage 2: ask clarifying questions and write a task definition")
    sp.add_argument("id", type=int)
    sp.add_argument("--no-questions", action="store_true")
    sp.add_argument("--voice", action="store_true", help="Hear the questions and answer by speaking")

    sp = add("analyze", cmd_analyze, "Stage 3: find affected code, edge cases and risks")
    sp.add_argument("id", type=int)

    sp = add("plan", cmd_plan, "Stage 4: break the task into subtasks and test cases")
    sp.add_argument("id", type=int)

    sp = add("go", cmd_go, "Run clarify → analyze → plan in one go")
    sp.add_argument("id", type=int)
    sp.add_argument("--no-questions", action="store_true")
    sp.add_argument("--redo", action="store_true", help="Redo stages that already ran")
    sp.add_argument("--voice", action="store_true", help="Hear the questions and answer by speaking")

    sp = add("prompt", cmd_prompt, "Stage 6: copy a ready prompt for Claude Code / Cursor")
    sp.add_argument("ref", help="Task (12) or subtask (12.3)")
    sp.add_argument("-o", "--out", help="Write to a file instead")
    sp.add_argument("--print", action="store_true", help="Print instead of copying")

    sp = add("exec", cmd_exec, "Stage 6: launch a coding agent on the task")
    sp.add_argument("ref", help="Task (12) or subtask (12.3)")
    sp.add_argument("--tool", choices=list(TOOLS), default="claude")
    sp.add_argument("-y", "--yes", action="store_true")

    sp = add("build", cmd_build, "Build it: the AI writes the code, runs the tests, fixes them and saves the "
                                 "work on a safe branch")
    sp.add_argument("task", nargs="*", help='What to build, in your own words, or a task number')
    sp.add_argument("--new", metavar="FOLDER", help="Start a brand-new project in this folder")
    sp.add_argument("--check", metavar="COMMAND", help="The command that proves it works (default: the "
                                                       "project's tests)")
    sp.add_argument("--voice", action="store_true", help="Say what to build")
    sp.add_argument("--simple", action="store_true", help="Explain the result without technical words")
    sp.add_argument("-y", "--yes", action="store_true", help="Allow installs and deletions without asking, "
                                                             "and keep the result if the tests pass")
    sp.add_argument("--max-steps", type=int, default=40, help=argparse.SUPPRESS)
    sp = add("step", cmd_step, "Add, edit, remove or list a task's steps yourself")
    sp.add_argument("action", choices=["add", "edit", "rm", "list", "show"])
    sp.add_argument("ref", help="Task (for add/list), or step like 3.2 (for edit/rm/show); `tm step 3.2` shows it")
    sp.add_argument("text", nargs="?", help="Step title (add), or new title (edit)")
    sp.add_argument("-d", "--details", help="What exactly to do in this step")
    sp.add_argument("-y", "--yes", action="store_true", help="Don't ask before removing")

    sp = add("start", cmd_start, "Mark a task as in progress")
    sp.add_argument("id", type=int)

    sp = add("done", cmd_done, "Mark a task (12) or subtask (12.3) done")
    sp.add_argument("ref")
    sp.add_argument("--undo", action="store_true", help="Reopen it instead")

    sp = add("sync", cmd_sync, "Stage 7: update progress from git commits and GitHub")
    sp.add_argument("id", type=int, nargs="?")
    sp.add_argument("--ai", action="store_true", help="Also let AI match commits without TM- refs")

    sp = add("verify", cmd_verify, "Stage 9: check the written code against the plan")
    sp.add_argument("id", type=int)
    sp.add_argument("--base", help="Check everything since this branch/commit (e.g. main) "
                                   "instead of commits mentioning TM-<id>")
    sp.add_argument("--strict", action="store_true",
                    help="Exit with code 1 if anything is missing or partial (for hooks and CI)")

    sp = add("calendar", cmd_calendar, "Calendar: export tasks as .ics, or get a Google Calendar link")
    sp.add_argument("action", choices=["export", "link"])
    sp.add_argument("id", nargs="?", type=int, help="Task number (for link)")
    sp.add_argument("-o", "--out", help="File to write (export; default ~/.taskmind/taskmind.ics)")
    sp.add_argument("-a", "--all", action="store_true", help="Include all done tasks, not just the last 30 days")
    sp.add_argument("--open", action="store_true", help="Open the link in your browser (link)")

    sp = add("remind", cmd_remind, "Reminders: list, set <id> <when>, on, off, check, watch")
    sp.add_argument("action", nargs="?", choices=["list", "set", "on", "off", "check", "watch"])
    sp.add_argument("id", nargs="?", help="Task number (for set)")
    sp.add_argument("when", nargs="*", help="When (for set): +30m, 18:00, tomorrow 9am, friday 10:30 ...")
    sp.add_argument("--speak", action="store_true", help="Also read reminders out loud")
    sp.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--every", type=int, default=60, help="Seconds between checks (for watch)")
    sp.add_argument("-y", "--yes", action="store_true")

    sp = add("today", cmd_today, "Stage 8: what to focus on today")
    sp.add_argument("--ai", action="store_true", help="Add an AI-written briefing")
    sp.add_argument("--speak", action="store_true", help="Read it out loud")

    sp = add("rm", cmd_rm, "Delete a task")
    sp.add_argument("id", type=int)
    sp.add_argument("-y", "--yes", action="store_true")
    return p


def safe_output():
    """Terminals that can't show emoji or other scripts (some Windows consoles, pipes) get a
    replacement character instead of a crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    global _DEPTH
    _DEPTH += 1
    try:
        return _main(argv)
    finally:
        _DEPTH -= 1
        if _DEPTH == 0:
            close_all()  # tidy up WAL files; lets Windows delete or replace the database


_DEPTH = 0


def _main(argv=None) -> int:
    safe_output()
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = sorted(next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices)
    # `tm step 1.3` / `tm step 1`: show that step or the task's steps.
    if len(argv) >= 2 and argv[0] == "step" and REF_RE.match(argv[1]):
        argv = ["step", "show", *argv[1:]]
    # A typo with normal arguments (`tm og 1`, `tm lst`): offer the fix right away, no AI needed.
    if argv and argv[0] not in commands and not argv[0].startswith("-") and all(
            REF_RE.match(a) or a.startswith("-") for a in argv[1:]):
        close = closest_command(argv[0], commands)
        if close:
            fixed = [close, *argv[1:]]
            if interactive() and ui.confirm(f"Did you mean `tm {' '.join(fixed)}`?", default=True):
                return main(fixed)
            ui.error(f"`{argv[0]}` isn't a TaskMind command. Did you mean `tm {' '.join(fixed)}`?")
            return 2
    # Words that aren't a command ("tm remind me tomorrow about task 1", "tm how do I add a task?")
    # go to the brain, which answers questions and turns requests into commands.
    if argv and argv[0] not in commands and not argv[0].startswith("-") and (
            len(argv) > 1 or " " in argv[0] or argv[0].endswith("?") or argv[0].lower() in QUESTION_WORDS):
        argv = ["do", *argv]
    try:
        args = parser.parse_args(argv)
    except UsageError as err:
        if looks_like_a_sentence(argv):  # "tm remind me about task 1 tomorrow": a request, not a typo
            return main(["do", *argv])
        ui.error(friendly_usage_error(err, commands, argv))
        return 2
    if not getattr(args, "func", None):
        ui.out(tutorial.OVERVIEW)  # plain `tm`: the short overview, not the full reference
        return 0
    try:
        ui.SIMPLE = config.load().get("mode") == "simple"
    except (ValueError, OSError):
        ui.SIMPLE = False
    maybe_greet(args)
    try:
        code = args.func(args)
    except BrokenPipeError:
        # Output was piped into something that stopped reading (e.g. `tm list | head`).
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
    except (CLIError, LLMError, github.GitHubError, voice.VoiceError, ValueError, RuntimeError,
            OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        ui.error(str(exc))
        if not isinstance(exc, CLIError):  # CLIErrors are usage mistakes, not failures worth reporting
            diagnostics.log_error(argv, exc)
        return 1
    except KeyboardInterrupt:
        ui.out("\nCancelled.")
        return 130
    except Exception as exc:  # noqa: BLE001 - a bug: say so kindly, keep the details for the report
        if diagnostics.is_debug():
            raise
        diagnostics.log_error(argv, f"{type(exc).__name__}: {exc}", exc)
        ui.error("Something went wrong inside TaskMind. That's a bug, not something you did. The details "
                 "were saved (without keys or code); run `tm report-bug` to send them. "
                 "Your tasks are safe: TaskMind keeps daily backups in ~/.taskmind/backups.")
        return 1
    return code or 0
