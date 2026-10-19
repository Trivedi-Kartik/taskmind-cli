# TaskMind

**Say what you want built. TaskMind builds it, tests it, and proves it works.**

```sh
cd ~/code/my-app
tm build "add a CSV export to the reports page"
```

TaskMind reads your project, writes the code and its tests, runs them, fixes what fails,
and saves the work on a separate git branch. Then it **checks the result itself**: a task
is only "done" when its checks pass, not when the AI says so. Press Enter to keep it.

Can't code? Describe what you want, in English, Hindi or Hinglish:

```sh
tm build --new my-shop --simple "Meri dukaan ke liye billing program: item, price, quantity, aur 18% GST ke saath bill"
```

It runs on free AI (Gemini, Groq) or any provider you have a key for, and switches to a
backup AI by itself when one hits its limit.

| | |
|---|---|
| **Proof, not promises** | TaskMind runs the tests itself; web pages are opened in a real (hidden) browser. Failures go back to the AI to fix |
| **Safe by default** | Work happens on its own branch. Risky commands need your yes; dangerous ones never run. Keys and secret files never reach the AI or git |
| **One command** | No git knowledge needed: unsaved work, leftovers and undo are handled for you |
| **Any AI, free ones included** | Gemini, Groq, OpenRouter, Ollama (local), OpenAI, Claude |
| **Your language** | Plain-word explanations in English, Hindi or Hinglish; speak instead of typing |

## Contents

- [Install and update](#install-and-update)
- [Set up your AI](#set-up-your-ai)
- [Build it: tm build](#build-it-tm-build)
- [Bigger work: plan first](#bigger-work-plan-first)
- [Just say what you want](#just-say-what-you-want)
- [More features](#more-features): reminders, calendar, voice, your own steps, everyday tasks
- [Command reference](#command-reference)
- [AI providers and models](#ai-providers-and-models)
- [Privacy and safety](#privacy-and-safety)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Install and update

You need **Python 3.9 or newer** and **git**. TaskMind uses only Python's standard library.

```sh
pipx install git+https://github.com/Trivedi-Kartik/taskmind-cli.git
tm --version
```

No pipx? `python3 -m pip install --user git+https://github.com/Trivedi-Kartik/taskmind-cli.git`.
Optional extras: `rich` for nicer output, `anthropic` to use Claude.

**Updating:** run `tm update`. It checks GitHub, shows what's new, and updates TaskMind the
right way for how you installed it. TaskMind also tells you, at most once a day, when a new
version is out (turn that off with `tm config --update-check off`). Updating never touches
your tasks, settings or keys: they live in `~/.taskmind`. What changed in each version is in
[CHANGELOG.md](CHANGELOG.md).

New to TaskMind? `tm tutorial` is a guided 10-minute first task on a practice project.

## Set up your AI

```sh
tm setup          # choose a provider, paste its key (hidden), pick a model, test, save
tm doctor --all   # check every provider you set up
```

You bring your own key; TaskMind never pays for or routes your AI usage. Free options:

- **Google Gemini:** key at https://aistudio.google.com/apikey. Roomy enough to build real
  projects, but free tiers cap **requests per day per model** (in October 2026 one model
  allowed only 20 a day; "Lite" models usually allow more). Your limits:
  https://ai.dev/rate-limit.
- **Groq:** key at https://console.groq.com/keys. Fast, but its free tier (8,000 tokens a
  minute) is too small for anything but small builds. A good backup and for planning.

Every provider you set up becomes an automatic backup. To build with one and keep the other
for everything else: `tm config --stage build=gemini`. For daily work on real projects, a
paid key (pay per use) removes the limits.

## Build it: tm build

```
$ tm build "let users reset their password by email"
🔨 Building TM-4 in ~/code/my-app
On a new branch, tm/let-users-reset-their-password: your main branch isn't touched until you keep the result.

  🔍 read file app/auth.py
  ✏️  app/auth.py
  ✏️  tests/test_reset.py
  ▶ python -m pytest -q
    exit code 1                      ← the AI reads the errors and fixes them
  ✏️  app/auth.py
  ▶ python -m pytest -q
    exit code 0

Checking it myself: python -m pytest -q
✅ Passed.

✅ Done and checked.
Checked by TaskMind: python -m pytest -q
Added a reset-password flow ... (the AI's summary)
Keep it (add it to your main branch)? [Y/n]
```

**How it works**

1. **Where:** inside your project. Outside one (say your home folder), TaskMind offers a
   new folder instead of touching what's there; `--new FOLDER` starts one directly.
2. **A safe branch:** the work happens on `tm/<task-name>`. Unsaved changes of yours are
   listed first, with an offer to save them, so nothing mixes.
3. **The AI works with tools:** it reads, searches, writes and edits files, and runs tests
   and scripts in the project folder. You see every action.
4. **TaskMind checks it:** your `--check` command, else the project's tests (`npm test`,
   `pytest`/`unittest`, `go test`, `cargo test`), else the tests the AI wrote. The AI can't
   swap in an easier check. **Web pages are also opened in a hidden browser** (Chrome,
   Chromium or Edge): JavaScript errors, missing files or a blank page count as failures.
5. **Fix rounds:** failures go back to the AI, up to 3 times.
6. **Saved:** the work is committed on its branch (never secret files like `.env`). Enter
   adds it to your branch. Changed your mind later? `tm build --undo`.

**The result, honestly**

| You see | It means |
|---|---|
| ✅ Done and checked | TaskMind's own checks passed (listed under "Checked by TaskMind") |
| ❌ Not done | Still failing after the fix rounds; the last errors are shown. Keeping it is your choice |
| 🟡 Built, but not proven | There was nothing to check with (no tests, no browser). Look before keeping it |
| 🟡 Not finished | The AI ran out of steps. The work so far is on its branch |
| ❌ Nothing was built | The AI stopped without writing anything. Try again |

**Commands the AI may run**

| Kind | Examples | What happens |
|---|---|---|
| Normal | tests, scripts, linters, `git status`, `git diff` | Runs |
| Risky | installing packages, deleting files, internet access, `docker` | Asks you first (`--yes` allows them) |
| Never | `sudo`, deleting outside the project, `git push`/`commit`/`reset`, piping a download into a shell | Refused |

Commands run in the project folder with no keyboard input and a 5-minute limit, and never
see your API keys, tokens or passwords.

**Options**

| Command | What it does |
|---|---|
| `tm build "..."` | Build it in this folder |
| `tm build 3` | Build task 3, following its plan (see [plan first](#bigger-work-plan-first)) |
| `tm build --new FOLDER "..."` | Start a new project in that folder |
| `--check "COMMAND"` | The command that proves it works, e.g. `--check "pytest tests/test_export.py"` |
| `--simple` | Explain the result without technical words |
| `--voice` | Say what to build |
| `-y`, `--yes` | Allow risky commands and keep the result if the checks pass (for scripts) |
| `tm build --more "..."` | Follow up on the last build; with no text, continue an unfinished one |
| `tm build --undo` | Take back the last build. An unfinished one is thrown away; a kept one is taken out of your branch and set aside on a `tm/undone-...` branch, so nothing is lost |

Summaries come in your language: `tm me --language hinglish` (or `hindi`, `english`).

**Tips:** describe the result you want and what "done" looks like; for websites, list the
sections and effects. Big or vague tasks go better [planned first](#bigger-work-plan-first).

### Follow-ups

Not quite right, or want more? Follow up on the last build, in plain words:

```sh
tm build "add online ordering to the menu"
tm build --more "make the order button bigger and green"
tm build --more "also send the order total by WhatsApp"
```

Each follow-up knows what the earlier rounds asked for, what changed and how it went, so
"make *that* button bigger" works. If the last build is unfinished (its checks failed, you
pressed Ctrl+C, or the AI hit a limit), `--more` carries on **on the same branch**. Run it
with no text to continue where it stopped:

```sh
tm build --more          # pick up an unfinished build
```

If you edited files by hand on an unfinished build's branch, TaskMind offers to keep your edits
as part of the work. Every round is still checked, and `tm build --undo` takes back the last one.

### Working on an existing project

**Project notes.** Every build first reads the notes a project keeps for AI tools:
`TASKMIND.md`, `CLAUDE.md`, `AGENTS.md`, `.cursorrules` and `.github/copilot-instructions.md`.
If your team already uses Claude Code, Codex or Cursor, TaskMind follows the same rules with
no setup. No notes yet? Run:

```sh
tm init          # studies the project and writes TASKMIND.md: what it is, stack, layout,
                 # how to run and test it, conventions, what's easy to break
```

Read what it wrote, fix anything wrong, and add your own rules ("always use TypeScript",
"never touch the payments folder"). It's saved in git, so your whole team shares it.

**Point at files with @.** Name the files or folders a change is about, and the AI starts from
exactly those:

```sh
tm build "add the same validation as @src/forms/login.ts to @src/forms/signup.ts"
tm build "the prices in @menu.js are wrong for cold drinks; fix them"
tm build "add a dark mode to every page in @public/"
```

A file is attached in full (a folder as its list of files). A mistyped name is caught before
the build starts. Secret files are never attached. The build shows what it started from:

```
  📘 Project notes: TASKMIND.md, CLAUDE.md
  📎 Starting from: src/forms/login.ts, src/forms/signup.ts
```

## Bigger work: plan first

For a large or unclear task, let TaskMind think it through before building:

```sh
tm add "Let users export their notes as CSV"   # task 1, linked to this project
tm go 1          # asks what's unclear, reads the code, runs a security check, makes steps 1.1, 1.2 ...
tm build 1       # builds the whole plan; the steps tick off when it passes
tm verify 1      # a second AI checks the code against the plan
```

- **`tm go`** asks up to 5 questions (Enter skips one), writes a definition with "done when"
  rules, lists affected files, edge cases and risks, and splits the work into steps.
- **The security check** looks at the same 7 things every time: input validation and
  injection, login and permissions, secrets, sensitive data, error handling, dependencies,
  and compatibility. Every risk becomes a step and a test.
- **`tm verify`** marks each part of the plan ✅ done, 🟡 partial, ❌ missing or ❔ unclear,
  with evidence. It prefers an AI other than the one that planned or wrote the code.
  `tm verify 1 --strict` fails on any gap (for CI or git hooks).
- **Prefer Claude Code, Cursor or Codex for the coding?** `tm prompt 1.1` gives them a
  complete brief for a step, and `tm exec 1.1 --tool claude` starts them with it.

TaskMind also plans everyday tasks, with reminders and calendar export: see
[More features](#more-features).

## Just say what you want

Type, or say, what you want in your own words, in any language:

```sh
tm "remind me about the CSV task tomorrow at 9 and make it high priority"
tm "kal subah 10 baje doctor appointment ka reminder laga do"
tm talk            # hands-free: speak, and hear the answers
```

TaskMind's AI picks **TaskMind's own commands** for it, shows them, and runs them only after
you agree. Commands that delete need an explicit yes; settings are never changed this way.
Questions work the same way: `tm ask "how do I set a reminder?"` answers from this guide,
even when no AI is available. `tm help <topic>` shows a topic offline.

## More features

### Reminders

Desktop notifications at the time you set, on a task's due date (at 09:00, change with
`tm config --remind-time`), and once a day while it's overdue, each with the next step.

```sh
tm remind on                     # turn notifications on (asks first); --speak reads them out
tm remind set 4 "friday 10:30"   # also: +30m, 18:00, "tomorrow 9am", 2026-10-20 14:00
tm remind off
```

`tm remind on` adds one line to your crontab so reminders arrive even with the terminal closed
(Linux, macOS); it never writes keys there. On Windows, keep `tm remind watch` running.

### Calendar

`tm calendar export` writes due dates and reminders to `~/.taskmind/taskmind.ics`; import it
once into Google, Apple or Outlook Calendar (it's an export, not live sync).
`tm calendar link 4 --open` opens a prefilled "add to Google Calendar" page for one task.

### Voice

`tm talk` is hands-free: press Enter, speak, press Enter; TaskMind shows what it will do and
answers out loud. `tm add --voice`, `tm build --voice`, `tm go 1 --voice` and
`tm today --speak` work too. Speech-to-text uses your Groq key (free); the natural voice needs a
one-time terms acceptance at https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english,
otherwise the computer's own voice is used. Recording needs `arecord` (Linux) or `sox` (macOS).
`tm voice --test` checks everything.

### Your own steps

A plan's steps are yours to change: `tm step add 3 "Write tests"`, `tm step edit 3.2 "..."`,
`tm step rm 3.4`, `tm step list 3`. Step numbers never change and are never reused, so commit
messages like `TM-3.4` keep pointing at the right step. TaskMind learns from your edits: steps
you keep adding appear in future plans (`tm me` shows what it learned).

### Everyday tasks and simple mode

Tasks without code (`tm add "Plan a trip to Goa" --no-repo`) get a plain-language plan: what's
needed, what's often forgotten, and steps you can each do in one sitting. `tm config --mode simple`
uses plain wording everywhere ("Task 5 step 2", "in progress") and never links tasks to code.

## Command reference

`<id>` is a task number (`12`); `<ref>` is a task or a step (`12.3`). `tm <command> --help`
shows every option.

| Command | What it does |
|---|---|
| `tm build "..." \| <id>` | Build it ([details](#build-it-tm-build)); `@file` mentions; `--more`, `--new`, `--check`, `--undo`, `--simple`, `--voice`, `-y` |
| `tm init` | Study this project and write `TASKMIND.md`, the notes every build reads first |
| `tm add "title"` | Write a task down (`--due friday`, `-p high`, `--remind "tomorrow 9am"`, `--no-repo`, `--voice`) |
| `tm list` / `tm show <id>` / `tm today` | Your tasks / everything about one / what to do today (`--speak` reads it out) |
| `tm go <id>` | Questions, code analysis, security check and steps (`--redo`, `--voice`) |
| `tm verify <id>` | Check the code against the plan (`--base main`, `--strict`) |
| `tm edit <id>` / `tm rm <id>` | Change a task / delete it |
| `tm step add\|edit\|rm\|list` | Change a task's steps yourself |
| `tm done <ref>` / `tm start <id>` | Tick off a task or step (`--undo` reopens) / mark in progress |
| `tm sync` | Tick steps off from commits that mention `TM-1.2` |
| `tm prompt <ref>` / `tm exec <ref>` | Brief for Claude Code or Cursor / start one with it |
| `tm import owner/repo` | Turn open GitHub issues into tasks |
| `tm "..."` / `tm talk` / `tm ask "..."` | [Say what you want](#just-say-what-you-want) / hands-free voice / ask a question |
| `tm remind` | Reminders: `set <id> <when>`, `on` (desktop notifications), `off` |
| `tm calendar export` / `link <id>` | `.ics` file for any calendar / "add to Google Calendar" link |
| `tm me` / `tm week` | Your profile and language / your week |
| `tm backup` / `tm export` | Back up or restore your tasks (also automatic, daily) / everything as JSON |
| `tm setup` / `tm doctor` / `tm models` | Set up an AI / test it / list its models |
| `tm config` | Settings (see below) |
| `tm update` | Install the newest TaskMind |
| `tm tutorial` / `tm help` | Guided first task / offline help topics |
| `tm report-bug` / `tm feedback` | Report a problem (no keys or code included) / tell us what to improve |

**Settings** (`tm config` shows them all):

```sh
tm config --provider gemini --model "Gemini 3.1 Flash Lite"   # names as the website shows them work
tm config --stage build=gemini          # a specific AI for one stage (build, clarify, analyze, plan, verify ...)
tm config --fallback gemini,groq        # backup order
tm config --mode simple                 # plain wording, for people who don't code
tm config --update-check off            # no daily "new version" note
tm config --remind-time 08:30           # daily reminder time
```

Everything lives in `~/.taskmind/` (`TASKMIND_HOME` changes it): `config.json` (settings and
keys, readable only by you) and `taskmind.db` (your tasks), with daily backups in `backups/`.

## AI providers and models

| Provider | Cost | Key |
|---|---|---|
| `gemini` (Google) | free tier | `GEMINI_API_KEY` |
| `groq` | free tier | `GROQ_API_KEY` |
| `openrouter` | free models available | `OPENROUTER_API_KEY` |
| `ollama` | free, runs on your computer | none |
| `openai` (ChatGPT) | paid | `OPENAI_API_KEY` |
| `anthropic` (Claude) | paid | `ANTHROPIC_API_KEY` (needs `pip install anthropic`) |
| `xai` (Grok) | paid | `XAI_API_KEY` |

Models come from the provider's live list (`tm models`), because providers retire models
often. Environment variables override saved keys.

**Never stuck:** when a provider fails (daily limit, outage, rejected key), the work continues
on the next one, and finished stages are never redone. Short per-minute limits are waited out
instead. `tm show <id>` lists which AI did what.

## Privacy and safety

- **Your data stays on your computer:** tasks, plans, keys and history. No account.
- **Code goes only to the AI you chose,** and only after you agree once per project. Use
  `ollama` to keep everything local.
- **Secrets never reach the AI:** files like `.env`, private keys and credentials are never
  read, and secret-looking values in code are replaced with `[REDACTED]`. Secret files are
  never committed by `tm build`, and its commands never see your keys.
- **Builds can't harm your work:** a separate branch, your yes for risky commands, and undo.
- **Update check:** once a day TaskMind fetches its own version number from GitHub. Nothing
  about you is sent. `tm config --update-check off` stops it.
- Voice recordings go to the speech-to-text provider (Groq or OpenAI) and are deleted locally
  right after. Reminders and calendar export run entirely on your computer.

## Troubleshooting

| Message | What to do |
|---|---|
| "needs an API key" / "isn't connected to an AI yet" | `tm setup` |
| "Daily free-tier limit reached ... try again in ..." | That model is done for today. Each model has its own allowance: switch (`tm models`, `tm config --model ...`), wait, or turn on billing |
| "Free-tier rate limit" / long pauses during a build | The per-minute limit; TaskMind waits and continues |
| "Model ... is not available" / "doesn't offer a model called" | `tm models`, then `tm config --model "<name>"` (quotes around names with spaces) |
| "API key was rejected" | Make a new key, then `tm setup` |
| "changes that aren't saved in git" | Answer y to let TaskMind save them, or commit them yourself |
| "still on an unfinished build" | Answer y to throw it away, or `tm build --undo` |
| A build went wrong | `tm build --undo` puts the folder back as it was |
| A build stopped part-way (limit reached, Ctrl+C) | `tm build --more` continues it |
| "doesn't understand: ..." | Put text with spaces in quotes |
| Every provider failed | `tm doctor --all` shows which one and why |
| "Something went wrong inside TaskMind" | A bug: `tm report-bug`. Your tasks are safe (daily backups) |
| No notifications / no sound | `tm remind` and `tm voice --test` show what's missing |
| Anything else | `tm ask "..."` in your own words |

## Development

```sh
python3 -m unittest discover -s tests     # 325 unit tests, no AI calls, no dependencies
python3 tests/live_smoke.py               # every command end to end, with your real AI provider
python3 scripts/build.py                  # refresh the bundled guide, build dist/taskmind.pyz
```

The tests run on Linux, macOS and Windows with Python 3.9 to 3.13 on every push. Manual
tests: [TESTING.md](TESTING.md). Plans: [ROADMAP.md](ROADMAP.md). Releases: bump
`taskmind/__init__.py` and add a [CHANGELOG.md](CHANGELOG.md) entry, so `tm update` can tell
people what's new.

## License

MIT © 2026 Kartik Trivedi. Free to use, change and share; see [LICENSE](LICENSE).
