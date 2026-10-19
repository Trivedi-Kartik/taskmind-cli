# TaskMind

**Say what you want built. TaskMind builds it, tests it, and proves it works.**

```sh
cd ~/code/my-app
tm                                  # opens a session: type what you want, ask questions, follow up
tm build "add a CSV export to the reports page"      # or one request at a time
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

## The session: just `tm`

In a terminal, typing `tm` opens a session in the current project, like talking to a colleague:

```
╭─ TaskMind 0.8.0 ───────────────────────────────────────────╮
│ 📁 coffice-cafe   🌿 master   🤖 Google Gemini / gemini-3.5 │
│ 📘 TASKMIND.md  🗺 36 code files                            │
│                                                             │
│ Say what you want built or changed, or ask a question.      │
│ /help for commands · Ctrl+C stops a build · Ctrl+D leaves   │
╰─────────────────────────────────────────────────────────────╯
tm ❯ where is the footer tagline set?
  → search code tagline
  index.html:576 sets it, inside <footer> ...
tm ❯ make it bold
↪ Following up on TM-2 (round 2): make it bold
  ...
✅ Done and checked.
Kept on master. /undo takes it back, /diff shows what changed, or say what to change next.
tm ❯ now a bit smaller too         ← remembers "it": no flags, no second command
```

- **Say what you want** and it's built, checked and kept. **Ask a question** and it's answered from your
  code without changing anything. Questions are told apart from requests by how they're worded; `/ask ...`
  and `/build ...` override the guess.
- **Follow-ups just work.** The session remembers what it has built, so "make *that* bigger" knows what
  *that* is. `/clear` forgets and starts fresh.
- **Safe, and quick.** Every build still runs on its own branch with checks. A build that passes is kept
  straight away (one message instead of two prompts), and `/undo` takes it back. One that fails is left on its
  branch: say what to change to carry on from there, or `/undo`.
- **Ctrl+C stops a build** and returns to the prompt; the work so far is saved.
- **Commands:** `/undo`, `/diff` (what the last build changed), `/status`, `/new FOLDER request` (a new project;
  the session moves into it), `/init`, `/index`, `/voice`, `/model`, `/clear`, `/help`, `/exit`. Tab completes
  them, the up arrow brings back earlier lines, and a trailing `\` continues a line. Any TaskMind command works
  too: `tm list`.
- Outside a terminal (pipes, scripts), plain `tm` still prints the short overview. `tm chat` always opens the
  session; `TASKMIND_NO_CHAT=1` makes plain `tm` print the overview.

## Contents

- [The session: just `tm`](#the-session-just-tm)
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

**Quick mode: a few AI requests, not dozens.** Step-by-step agents make one AI request per step and re-send
the whole conversation each time, so even a small change can use 25 to 60 requests. On a free tier that is one
task a day. By default `tm build` works differently: TaskMind finds the code itself (project notes, the files you
pointed at with `@`, the code index, or the whole project when it's small), sends it once, and the AI answers with
the edits. TaskMind applies them, runs the project's checks, and sends back only what failed.

```
  ⚡ Quick mode: I find the code myself and the AI answers with the edit, in a few requests instead of dozens
  🔎 The AI asked to see: Index.summary
  ✏️  taskmind/codeindex.py
  ✏️  taskmind/cli.py
✅ Done and checked.
⚡ Quick mode: 5 AI requests, about 22k tokens
```

What it measured so far (Gemini Flash Lite): a tiny change in a small site took **3 requests, 7 seconds and
about 4,000 tokens**; a two-file change in a 36-file project took **5 requests and 8 seconds** in an experiment,
against 25 to 44 steps and 4 to 10 minutes step by step, with the same correct result. Bigger, multi-file
tasks are harder for it: the AI sometimes writes edit text that doesn't match the file, and TaskMind then
shows it the real text and asks again, or hands over to the step-by-step mode by itself and says why (the AI
says it needs exploring, its edits won't apply, or the checks still fail after two quick fixes). New projects
and providers with a tiny memory (Groq's free tier) always work step by step.
`tm build --agent` (or `TASKMIND_QUICK=off`) forces the step-by-step mode. Every build ends with the number of AI
requests and tokens it used, so you can see which mode is cheaper for your own tasks.

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
| `--agent` | Work step by step with tools instead of [quick mode](#build-it-tm-build): slower, many more AI requests |
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

### Big projects: the code index

In a project with 8 or more code files, TaskMind keeps an index of the code (outside your project,
in `~/.taskmind`, refreshed by reading only the files that changed). It records every function,
class, method and constant with its line numbers, and which files import which (Python,
JavaScript/TypeScript and HTML; other languages get definitions only). Secret files are never indexed
and no code is stored, only names and line numbers.

Each build starts with what the index knows, worked out by TaskMind itself with no extra AI request:

```
  🗺  Code index: 1,240 files, 9,800 definitions
  🎯 Probably relevant: services/billing/invoice.py, services/billing/tax.py
  📄 Handed to the AI up front: services/billing/invoice.py:compute_invoice_total
```

- **A map** of the project's most connected files and their main definitions.
- **The files your request probably concerns**, matched from the names in your words, with the
  matching definitions and their lines. Names written like code (`compute_total`, `Cart.total`, or
  anything in backticks) count most.
- **The code of the best-matching functions**, on providers with room for it, so the AI starts from it
  instead of searching.
- **`find_symbol` and `file_outline` tools** for the AI (30+ code files): where something is defined and
  who imports it, and a file's structure, in one step.

```sh
tm index                        # what TaskMind knows: files, definitions, links, most-imported files
tm index --find compute_total   # where it's defined, and who uses that file
tm index --outline src/app.py   # a file's definitions, imports and importers
tm index --rebuild              # throw the saved index away and read everything again
```

It also fixed a real blind spot: TaskMind used to list only the first 5,000 files of a project, so in a
12,000-file repository whole folders were invisible and searching them found nothing. Now every file is
listed, and a search that takes over 8 seconds stops and says so.

**Measured, honestly:** the same change to TaskMind itself (36 files, 12,000 lines) with Gemini Flash Lite.
With the index off the AI used 44 search/read steps and 12 test runs and ran out of steps in just under
10 minutes; with it on, 25 steps and 2 test runs, finished and checked in 3.5 minutes. That's one run each:
a sign, not a proof. An earlier version that only *offered* lookup tools made no difference, because the
model never called them; handing over the code is what helped. Switch the index off with
`TASKMIND_INDEX=off` to compare on your own project.

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
| `tm` / `tm chat` | The [session](#the-session-just-tm): say what you want, ask questions, follow up |
| `tm build "..." \| <id>` | Build it ([details](#build-it-tm-build)); `@file` mentions; `--more`, `--new`, `--check`, `--undo`, `--simple`, `--voice`, `-y` |
| `tm init` | Study this project and write `TASKMIND.md`, the notes every build reads first |
| `tm index` | The code index of this project; `--find NAME`, `--outline FILE`, `--rebuild` ([details](#big-projects-the-code-index)) |
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
| The index looks wrong or out of date | `tm index --rebuild`; or `TASKMIND_INDEX=off` to run without it |
| "doesn't understand: ..." | Put text with spaces in quotes |
| Every provider failed | `tm doctor --all` shows which one and why |
| "Something went wrong inside TaskMind" | A bug: `tm report-bug`. Your tasks are safe (daily backups) |
| No notifications / no sound | `tm remind` and `tm voice --test` show what's missing |
| Anything else | `tm ask "..."` in your own words |

## Development

```sh
python3 -m unittest discover -s tests     # 418 unit tests, no AI calls, no dependencies
python3 tests/live_smoke.py               # every command end to end, with your real AI provider
python3 scripts/build.py                  # refresh the bundled guide, build dist/taskmind.pyz
```

The tests run on Linux, macOS and Windows with Python 3.9 to 3.13 on every push. Manual
tests: [TESTING.md](TESTING.md). Plans: [ROADMAP.md](ROADMAP.md). Releases: bump
`taskmind/__init__.py` and add a [CHANGELOG.md](CHANGELOG.md) entry, so `tm update` can tell
people what's new.

## License

MIT © 2026 Kartik Trivedi. Free to use, change and share; see [LICENSE](LICENSE).
