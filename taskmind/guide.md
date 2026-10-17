# TaskMind

**Say what you want built. TaskMind builds it, tests it, and proves it works.**

```sh
cd ~/code/my-app
tm build "add a CSV export to the reports page"
```

TaskMind reads your project, writes the code and its tests, runs them, fixes what
fails, and saves the work on a separate git branch. Then it **runs the tests itself**:
a task is only "done" when they pass, not when the AI says so. Press Enter to keep the
result; your own branch is untouched until you do.

Can't code? Describe the app, in English, Hindi or Hinglish, and TaskMind makes a new project for it:

```sh
tm build --new my-shop --simple "Meri dukaan ke liye billing program: item, price, quantity, aur 18% GST ke saath bill"
```

It works with free AI (Groq's free tier by default) or any provider you have a key
for, and switches to a backup AI on its own when one hits its limit.

### What makes it different

| | |
|---|---|
| **Proof, not promises** | TaskMind runs the tests itself after the AI says it's finished. If they fail, the errors go back to the AI to fix (up to 3 rounds). If the AI changed tests that already existed, you're told to look before keeping. |
| **Safe by default** | The work happens on its own branch. Installs, deletions and network commands need your yes; `sudo`, wiping folders and `git push` never run. Your API keys are hidden from the commands the AI runs, and secret files are never read, sent or committed. |
| **Any AI, including free** | Groq, Gemini, OpenRouter, Ollama (local), OpenAI, Claude. When one fails or runs out, the build continues on the next. |
| **Your language** | Explanations in plain words for non-programmers, in English, Hindi or Hinglish; say the task out loud with `--voice`. |
| **Bigger work, planned first** | For a large task, `tm go` asks the right questions, reads the code, runs a security check and makes a step-by-step plan; `tm build <task number>` then builds the whole plan. |

### TaskMind in 2 minutes

Every command starts with `tm`.

| Command | In plain words | Example |
|---|---|---|
| `tm build "..."` | **Build it**: write the code and tests, run them, fix them, save the work | `tm build "add a dark mode toggle"` |
| `tm build --new FOLDER "..."` | Make a brand-new project | `tm build --new todo-app "a to-do list in the terminal"` |
| `tm add "..."` | Write a task down for later. It gets a number (1, 2, 3 ...) | `tm add "Fix the login bug"` → task 1 |
| `tm go 1` | Think task 1 through: questions, code reading, risks, a step-by-step plan | `tm go 1` |
| `tm build 1` | Build task 1, following its plan | `tm build 1` |
| `tm list` | Show my to-do list | `tm list` |
| `tm verify 1` | A second AI checks the finished code against the plan | `tm verify 1` |
| `tm today` | What should I do today? | `tm today` |
| `tm ask "..."` | Ask anything about using TaskMind, in your own words | `tm ask "how do I set a reminder?"` |
| `tm "..."` | **Just say what you want**: TaskMind works out its own commands and runs them after you agree | `tm "remind me about the CSV task tomorrow at 9"` |

**Numbers:** `1` is task 1, `1.2` is task 1 step 2, and `TM-1.2` is the same thing written
in commit messages and on screen.

**Stuck?** Ask in your own words: `tm ask "what do I do next on task 1?"`, or just
`tm how do I add a task`. See [Getting help](#getting-help).

**New?** Run `tm tutorial`: a guided 10-minute first task on a practice project. Typing
just `tm` shows the overview.

### Next to Claude Code, Codex or Cursor

Honestly: with a top paid model, those are stronger coding agents, and if you're
happy with one, keep using it. TaskMind is for when you want:

- to build with **free models** (or whichever AI you already pay for), without a subscription;
- **proof**: a build counts as done only when TaskMind's own test run passes;
- **one command**, safely on a branch, with nothing to approve except risky commands;
- to describe the work **in your own language**, including people who don't program;
- your tasks, plans and history to **stay yours**, on your computer, whichever AI you use.

It also works with them: `tm prompt` and `tm exec` hand a planned step to Claude Code,
Cursor or Codex ([details](#working-with-claude-code-cursor-and-codex)).

---


## Contents

- [TaskMind in 2 minutes](#taskmind-in-2-minutes)
- [Build it: tm build](#build-it-tm-build)
- [Install](#install)
- [First-time setup](#first-time-setup)
- [Your first task, step by step](#your-first-task-step-by-step)
- [How a task moves through TaskMind](#how-a-task-moves-through-taskmind)
- [Command reference](#command-reference)
- [Everyday tasks and simple mode](#everyday-tasks-and-simple-mode)
- [Reminders](#reminders)
- [Calendar](#calendar)
- [Just say what you want](#just-say-what-you-want)
- [TaskMind gets to know you](#taskmind-gets-to-know-you)
- [Your own steps](#your-own-steps)
- [Getting help](#getting-help)
- [Voice](#voice)
- [Working with Claude Code, Cursor and Codex](#working-with-claude-code-cursor-and-codex)
- [Security check](#security-check)
- [Verify](#verify)
- [AI providers and models](#ai-providers-and-models)
- [Never stuck: automatic backups](#never-stuck-automatic-backups)
- [Independent review](#independent-review)
- [A different AI for each stage](#a-different-ai-for-each-stage)
- [Free-tier limits and saving tokens](#free-tier-limits-and-saving-tokens)
- [Tips for the best results](#tips-for-the-best-results)
- [Configuration reference](#configuration-reference)
- [Privacy](#privacy)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Build it: tm build

```sh
cd ~/code/my-app                       # your project (a git repo; commit your own changes first)
tm build "let users reset their password by email"
```

What you see, step by step:

```
🔨 Building TM-4 in ~/code/my-app
On a new branch, tm/let-users-reset-their-password: your main branch isn't touched until you keep the result.

  🔍 list files .
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

✅ Done and checked: the tests pass.
Added a reset-password flow ... (the AI's summary)
Keep it (add it to your main branch)? [Y/n]
```

**How it works**

1. **A project folder.** Run it inside your project. Outside one (for example your home
   folder), TaskMind makes a new folder for the work (it asks for the name) instead of touching
   what's there. An empty folder becomes the project.
2. **A safe branch.** TaskMind creates `tm/<task-name>` and works there. If the project
   has changes you haven't committed, it stops and asks you to commit them first, so its
   work never mixes with yours.
3. **The AI works with tools:** it lists, searches and reads files, writes and edits
   them, and runs commands (tests, scripts) in the project folder. You see every action.
4. **The check.** When the AI says it's finished, TaskMind runs the test command itself.
   Which command? Yours if you gave `--check`, otherwise the project's own (`npm test`,
   `pytest`/`unittest`, `go test`, `cargo test`), otherwise the one the AI wrote tests for.
   The AI can't swap in an easier check than yours or the project's.
5. **Fix rounds.** If the check fails, the errors go back to the AI (up to 3 rounds).
6. **Saved and kept.** The work is committed on the branch (secret files like `.env` are
   never committed). Enter merges it into your branch and removes the work branch. Say no
   and you can throw it away, or leave it on its branch to look at
   (`git diff main...tm/<name>`).

**The result, honestly**

| You see | It means |
|---|---|
| ✅ Done and checked | TaskMind's own test run passed. The task is marked done |
| ❌ Not done | The tests still fail after the fix rounds; the last errors are shown. Keeping it is your choice (the default is no) |
| 🟡 Built, but not proven | There was nothing to run (no tests). Look at it before keeping it |
| 🟡 Not finished | The AI ran out of steps. The work so far is saved on its branch |

**Commands the AI may run**

| Kind | Examples | What happens |
|---|---|---|
| Normal | tests, scripts, linters, `git status`, `git diff` | Runs |
| Risky | installing packages, deleting files, internet access, `docker`, stopping programs | Asks you first (`--yes` allows them) |
| Never | `sudo`, deleting outside the project, `git push`/`commit`/`checkout`/`reset`, piping a download into a shell, erasing disks, shutting down | Refused; the AI is told to find another way |

Every command runs in the project folder with no keyboard input and a 5-minute limit
(servers and watchers are stopped, so the AI is told to prove things with tests). Your
API keys, tokens and passwords are removed from the environment the commands see.

**Options**

| Option | What it does |
|---|---|
| `tm build "..."` | Build it in the current folder |
| `tm build 3` | Build task 3, using its description, definition and plan (`tm go 3` first for a big task) |
| `--new FOLDER` | Start a new project in that folder (it gets its own git history) |
| `--check "COMMAND"` | The command that proves it works, e.g. `--check "pytest tests/test_export.py"` |
| `--simple` | Explain the result without technical words (on automatically in simple mode) |
| `--voice` | Say what to build |
| `-y`, `--yes` | Allow risky commands without asking, and keep the result if the tests pass. For scripts |

The summary is written in your language: set it with `tm me --language hinglish`
(or `hindi`, `english`).

**Which AI builds?** The same providers and backups as everything else; to use a
specific one just for building: `tm config --stage build=gemini` (see
[A different AI for each stage](#a-different-ai-for-each-stage)).

**How well does it work? The honest scoreboard**

Real tasks, run live on Groq's **free** models (`openai/gpt-oss-120b`, with `gpt-oss-20b` as
backup), each graded by a hidden check that the AI never saw (2026-10-17):

| Task | Who it's for | TaskMind said | Hidden check | Time |
|---|---|---|---|---|
| Add a command-line interface (add / done / list, clear error for a wrong id) to a Python to-do app | Developer, new feature | ✅ Done and checked | ✅ Pass | 3 min |
| Fix a slugify bug: repeated dashes, dashes at the ends, accented letters | Developer, bug fix | ✅ Done and checked | ✅ Pass | 1.5 min |
| Write `parseDuration("1h30m")` in JavaScript, with errors for bad input, and tests | Developer, new code (JS) | ✅ Done and checked | ✅ Pass | 1 min |
| "Meri dukaan ke liye billing program..." (Hinglish, new project, `--simple`) | Someone who can't code | ✅ Done and checked; explained in Hinglish | ✅ Correct bill and GST (checked by hand) | 1.5 min |

What this does **not** show yet: big real-world projects, and how it compares with Claude
Code on the same tasks. Those are next ([ROADMAP](ROADMAP.md)). One thing found by running
it for real: a free model sometimes "finishes" by writing text instead of calling the
finish tool, and TaskMind used to report that as unfinished. Now the tests decide.

Free tiers are slower than paid models: Groq's free tier allows 8,000 tokens a minute, so
TaskMind sometimes waits for the next minute. A paid or roomier model (Gemini's free tier,
OpenRouter, Claude) is faster on big tasks.

## Install

You need **Python 3.9 or newer** (tested on 3.9 to 3.13) and **git**. TaskMind uses only
Python's standard library; nothing else is required.

**Option 1: install the `tm` command (recommended)**

```sh
pipx install git+https://github.com/Trivedi-Kartik/taskmind-cli.git     # straight from GitHub
pipx install "taskmind-cli[all] @ git+https://github.com/Trivedi-Kartik/taskmind-cli.git"  # + rich and Claude
pipx install taskmind-cli                 # from PyPI, once it's published there
```

No pipx? `python3 -m pip install --user taskmind-cli` works too.

**Option 2: one file, no install**

```sh
python3 taskmind.pyz --help               # download taskmind.pyz and run it
chmod +x taskmind.pyz && ./taskmind.pyz   # or make it executable
```

Build that file yourself with `python3 scripts/build.py` (it appears in `dist/`).

**Option 3: from a source checkout**

```sh
git clone https://github.com/Trivedi-Kartik/taskmind-cli.git
cd taskmind-cli
./tm --help
ln -s "$PWD/tm" ~/.local/bin/tm           # optional: use `tm` from anywhere
```

Extras: `rich` makes output prettier (it works without); `anthropic` is only needed to use
**Claude** as your AI (`pip install anthropic`, or the `[claude]` extra above).

## First-time setup

```sh
tm setup
```

The guided setup:

1. lists the AI providers and what they cost (free ones first),
2. shows where to get an API key and lets you paste it (hidden as you type),
3. fetches the provider's **live** model list and marks the recommended model,
4. tests the connection, then saves,
5. the first time, asks whether you write code. Answer no for
   [simple mode](#everyday-tasks-and-simple-mode) (plain wording, no git).

Don't have a key? The fastest free option is **Groq**: sign up at
https://console.groq.com/keys (no card needed), then choose provider 1.

> **Tip: run `tm setup` a second time with another free provider** (for example
> Gemini). Every provider you set up becomes an automatic backup for when limits
> run out, and an independent reviewer for `tm verify`.

Check the connection any time with `tm doctor` (or `tm doctor --all` to test every
provider). If you run an AI command before setting up, TaskMind offers to run
`tm setup` for you.

## Your first task, step by step

```sh
cd ~/code/my-app                         # 1. go to your project (a git repo)
tm add "Let users export their notes as CSV" -p high --due +3d
#   → Added TM-1 (repo: ~/code/my-app)    tasks link to the current repo automatically

tm go 1                                  # 2. clarify → analyze → plan
```

`tm go` runs three stages and saves each one as it finishes:

- **Clarify:** asks up to 5 questions (press Enter to skip one), then writes a
  task definition and acceptance criteria.
- **Analyze:** an agent searches and reads your code (you see each step), then
  lists affected files, edge cases, risks and the [security check](#security-check).
- **Plan:** small subtasks (TM-1.1, TM-1.2, ...) with estimates, plus test cases.

```sh
tm prompt 1.1                            # 3. copy a ready prompt for subtask 1
#   paste it into Claude Code / Cursor, or: tm exec 1.1 --tool claude

git commit -m "Add CSV export endpoint TM-1.1"   # 4. mention the subtask in commits
tm sync                                  # 5. TM-1.1 is marked done from the commit

tm verify 1                              # 6. check the code against the plan
tm today                                 # 7. what to work on next
```

Everyday tasks work too: `tm add "Plan team offsite" --no-repo`, then `tm go`.
They get a plain-language planner instead of code analysis; see
[Everyday tasks](#everyday-tasks-and-simple-mode).

Prefer talking? `tm add --voice`, `tm go 1 --voice` and `tm today --speak`; see
[Voice](#voice).

## How a task moves through TaskMind

```
 capture ─► clarify ─► analyze ─► plan ─► hand off ─► track ─► verify
 tm add     ╰──────── tm go ───────╯     tm prompt   tm sync  tm verify
 tm import                               tm exec     tm done
```

Statuses: `inbox` → `clarified` → `analyzed` → `planned` → `in_progress` → `done`.
A task only moves forward. Each stage's result is saved before the next starts, so
if anything fails (an AI limit, your laptop restarts) you rerun just that stage.

## Command reference

`<id>` is a task number (`12`). `<ref>` is a task (`12`) or a subtask (`12.3`).
Run `tm <command> --help` for details.

### Setup and AI

| Command | What it does |
|---|---|
| `tm` | Short overview of the main commands |
| `tm tutorial` | Guided first task on a practice project (your real tasks are untouched) |
| `tm "request"` or `tm do "request" [--voice] [--dry-run] [-y]` | Say what you want; TaskMind proposes the commands and runs them after you agree ([details](#just-say-what-you-want)) |
| `tm wishlist` | Requests TaskMind couldn't do yet |
| `tm me [--name] [--language] [--hours] [--coder]` | Your profile ([details](#taskmind-gets-to-know-you)) |
| `tm week` | Your week: finished, steps done, what's slipping |
| `tm greet [--notify] [--speak] [--install \| --uninstall]` | Your day in one line; `--install` greets you each time you log in (Linux) |
| `tm backup [-o FILE] [--list] [--restore FILE]` | Back up or restore your task list (also automatic, daily) |
| `tm export [-o FILE]` | Every task, step and activity as JSON |
| `tm report-bug ["what happened"]` | Save a bug report (version, system, recent errors; no keys, no code) to send |
| `tm feedback "..."` | Tell the TaskMind team what would make it better |
| `tm ask "question"` | Ask anything about TaskMind or your tasks; answers from the guide even without a working AI |
| `tm help [topic]` | Help topics from this guide, offline (`tm help reminders`) |
| `tm setup` | Guided setup: choose a provider, add its key, pick a model, test, save |
| `tm doctor [--all]` | Test the main provider (`--all`: every backup too) |
| `tm models [--provider P] [--all]` | Live list of models (`--all` includes non-chat models) |
| `tm config` | Show settings |
| `tm config --provider P --model M --key K` | Switch provider / model, or set a key |
| `tm config --fallback groq,gemini` | Backup order ([details](#never-stuck-automatic-backups)) |
| `tm config --stage analyze=anthropic:claude-opus-5-5` | AI for one stage ([details](#a-different-ai-for-each-stage)) |
| `tm config --coder anthropic` | Which AI writes your code ([details](#independent-review)) |
| `tm config --review auto\|off\|P[:M]` | Who runs `tm verify` |
| `tm config --mode developer\|simple` | Plain wording for everyday use ([details](#everyday-tasks-and-simple-mode)) |
| `tm config --remind-time HH:MM` | When due-date and overdue reminders arrive (default 09:00) |
| `tm config --voice-output auto\|groq\|local\|off --voice-name NAME` | How TaskMind speaks ([details](#voice)) |
| `tm voice [--test]` | Check microphone, speech-to-text and voice; `--test` tries them |
| `tm talk` | Hands-free: speak requests, hear the answers ([details](#voice)) |

### Capture

| Command | What it does |
|---|---|
| `tm add "title" [-d text] [--due D] [-p high\|medium\|low] [--repo PATH \| --no-repo]` | New task. Links the current git repo unless `--repo`/`--no-repo`. Due: `YYYY-MM-DD`, `today`, `tomorrow`, a weekday (`friday`), `+3d` |
| `tm add ... --remind WHEN` | Add with a reminder: `+30m`, `18:00`, `9am`, `tomorrow 9am`, `friday 10:30`, `2026-10-20 14:00` |
| `tm add --voice [options]` | Say the task; TaskMind fills in title, description, due date and priority and asks before saving. Options you type win over what you said |
| `tm import owner/repo [--repo PATH] [--label L] [--assignee U] [--limit N]` | Import open GitHub issues (skips ones already imported). Private repos need `GITHUB_TOKEN` or `gh auth login` |

### Think it through (uses AI)

| Command | What it does |
|---|---|
| `tm go <id> [--no-questions] [--redo] [--voice]` | Clarify → analyze → plan, skipping stages already done (`--redo` reruns them). `--voice`: hear the questions, answer by speaking |
| `tm clarify <id> [--no-questions] [--voice]` | Questions, then definition + acceptance criteria |
| `tm analyze <id>` | Code analysis, edge cases, risks, security check |
| `tm plan <id>` | Subtasks, estimates, priority, test cases |

### Do the work

| Command | What it does |
|---|---|
| `tm build "..." \| <id> [--new FOLDER] [--check CMD] [--simple] [--voice] [-y]` | [Build it](#build-it-tm-build): write the code and tests, run and fix them, save the work on a branch |
| `tm prompt <ref> [--print] [-o FILE]` | Copy a hand-off prompt to the clipboard (or print / save it) |
| `tm exec <ref> [--tool claude\|cursor\|codex] [-y]` | Launch that coding agent in the repo with the prompt (asks first) |
| `tm step add <id> "title" [-d details]` | Add your own step (works with or without an AI plan) |
| `tm step edit <id.n> ["new title"] [-d details]` | Change a step |
| `tm step rm <id.n> [-y]` | Remove a step |
| `tm step list <id>` (or `tm step <id>`) | Show a task's steps |
| `tm step <id.n>` | Show one step in full, with how to tick it off |
| `tm start <id>` | Mark in progress |
| `tm done <ref> [--undo]` | Mark a task or subtask done (or reopen it) |

### Track and check

| Command | What it does |
|---|---|
| `tm sync [id] [--ai]` | Update progress from `TM-` commit references and closed GitHub issues. `--ai` also matches commits without references (uses AI) |
| `tm verify <id> [--base BRANCH] [--strict]` | Check the code against the plan ([details](#verify)) |
| `tm today [--ai] [--speak]` | Overdue, due today, in progress with the next step, ready to start, needs thinking, finished in 24h. `--ai` adds a short briefing; `--speak` reads it out loud |

### Reminders and calendar

| Command | What it does |
|---|---|
| `tm remind` | Show reminders, due dates and whether notifications are on |
| `tm remind set <id> <when>` | Set a reminder, e.g. `tm remind set 4 tomorrow 9am` |
| `tm remind on [--speak] [-y]` / `tm remind off` | Turn desktop notifications on or off (checks every 5 minutes, even with the terminal closed) |
| `tm remind check [--speak]` | Send any reminders that are due right now |
| `tm remind watch [--every SECONDS] [--speak]` | Keep checking in this terminal (Windows, or without cron) |
| `tm calendar export [-o FILE] [-a]` | Write due dates and reminders to an `.ics` file for Google, Apple or Outlook Calendar |
| `tm calendar link <id> [--open]` | "Add to Google Calendar" link for a task |

### Manage

| Command | What it does |
|---|---|
| `tm list [-a] [-s STATUS]` | Task board (`-a` includes done tasks) |
| `tm show <id>` | Everything about a task: definition, analysis, security, plan, verification, which AI did what, activity |
| `tm edit <id> [--title] [-d] [--due] [-p] [--repo] [--status] [--remind]` | Change details (`--due ""`, `--repo ""`, `--remind ""` clear) |
| `tm rm <id> [-y]` | Delete a task |

## Just say what you want

You don't have to remember commands. Type (or say) what you want, in any language:

```sh
tm "remind me about the CSV task tomorrow at 9 and make it high priority"
tm "add three tasks: buy milk, call mom, and pay rent by friday"
tm "kal subah 10 baje doctor appointment ka reminder laga do"
tm "move the rent task to next monday and add a step to transfer the money"
tm do --voice                     # say it instead of typing
```

TaskMind's AI works out which of **its own commands** do that, and shows you before
anything happens:

```
I'll run:
  1. tm edit 1 -p high            Set task TM-1 to high priority
  2. tm remind set 1 tomorrow 9am Create reminder for the CSV task
Go ahead? [Y/n]
```

How it stays safe and predictable:

- **The AI only proposes; TaskMind's normal commands do the work.** Every proposed
  command is checked against the real command list first; invalid ones are sent back
  to the AI to correct, and never run.
- **You confirm first.** Anything that deletes is marked ⚠ and needs an explicit `y`
  (Enter means no). Unclear answers are asked again, never guessed (y/n, yes/no and
  haan/nahi all work). Plans that only *show* things (`list`, `show`, `today` ...) run
  straight away.
- **Some commands are never run for you**: settings and keys (`config`, `setup`), turning
  reminders on or off (they change your crontab), and launching coding agents (`exec`).
- If it can't tell which task you mean, it asks one short question.
- It runs the commands in order and **stops at the first one that fails**.
- `--dry-run` only shows the plan; `-y` skips the question (for scripts).

**Things TaskMind can't do** (send emails, edit code, buy things ...) get an honest
"can't do that yet" and are saved to your **wishlist**: `tm wishlist` lists them. As the
person deciding what TaskMind should do next, that list tells you what people want.

Without a working AI, normal commands still work exactly as before, and requests get the
matching part of this guide instead.

## TaskMind gets to know you

No account or sign-up: everything stays on your computer.

```sh
tm me                      # name, language (auto/english/hindi/hinglish), working hours, coding tool
tm week                    # what you finished this week, and what's slipping
tm greet --install         # a greeting with your day each time you log in (Linux)
```

- **A daily greeting.** The first time you use `tm` each day: *"Good morning, Kartik! 1 due
  today · 1 overdue. Tip: TM-1 is 4 days overdue → tm edit 1 --due +2d"*.
- **Smart suggestions** in `tm today` and the greeting, from your own history: overdue
  tasks, tasks stuck for days, tasks added but never thought through, finished code that
  was never verified, reminders set while notifications are off.
- **Your profile** shapes the answers of `tm ask` and `tm "..."` (your name, your language).
  `tm setup` asks your name once.
- **It learns how you plan.** When you remove steps from an AI plan or add your own
  (`tm step`), TaskMind notices: if you often delete steps it plans fewer, larger ones; steps
  you keep adding (like "Write tests") appear in future plans. `tm me` shows what it learned.

All of this is computed locally from your task list: instant, free, and no AI calls.

## Your own steps

`tm go` plans steps for you, but the plan is yours to change:

```sh
tm step add 3 "Write the README section" -d "Explain the new export button"
tm step edit 3.2 "Ask design for the icon"     # new title (and/or -d for details)
tm step rm 3.4                                  # asks first; -y to skip the question
tm step list 3
```

You can also plan a task entirely by hand, without AI: add it with `tm add`, then add the
steps with `tm step add`.

- New steps go at the end. **Step numbers never change and are never reused**: if you
  remove step 3.4, the next new step is 3.9 (say), not 3.4. That keeps commit messages
  like `TM-3.4` pointing at the right step.
- Adding a step to a finished task reopens it; removing the last open step finishes it.
- `tm go <id> --redo` replaces the whole plan with a new AI plan (numbered from 1 again).
  After that, `tm sync` only counts commits made after the new plan, so old `TM-3.1`
  commits can't tick off the new step 3.1.

## Getting help

| Need | Command |
|---|---|
| The main commands at a glance | `tm` |
| Learn by doing, step by step | `tm tutorial` |
| Ask anything, in your own words (any language) | `tm ask "how do I export to my calendar?"` |
| Read a topic from this guide | `tm help` (all topics), `tm help reminders` |
| Every command and option | `tm --help`, `tm <command> --help` |

For *doing* things in your own words, see [Just say what you want](#just-say-what-you-want).

`tm ask` knows your setup and your tasks, so "what's left on task 1?" or "my key isn't
working" get answers about **your** situation. It only suggests commands that really
exist, and never shows or asks for your API key.

**It still answers when the AI doesn't work** (no key yet, key rejected, limit reached,
offline): it then shows the matching part of this guide instead, and for key problems
the exact steps to fix them.

You can also just type a question after `tm`: `tm how do I add a task` is the same as
`tm ask "how do I add a task"`. Mistyped commands are fixed on the spot, without AI
(`tm og 1` → "Did you mean `tm go 1`? [Y/n]"), and missing pieces are explained in plain words (`tm go` → "`tm go`
needs a task number, e.g. `tm go 1`").

## Everyday tasks and simple mode

TaskMind is built for developers, but it plans any task. Tasks without a code repo
(`tm add "..." --no-repo`, or every task in simple mode) get a **plain-language
planner** instead of the engineering one:

- **Clarify** asks practical questions (dates, people, budget, preferences).
- **Think it through** lists what's needed (documents, bookings, people, money,
  time), what usually gets forgotten, and what could go wrong. No code analysis or
  security check.
- **Plan** gives concrete steps you can each do in one sitting (15 minutes to 2
  hours), with steps that have deadlines or waits (bookings, approvals) first, and
  "Done when" checks instead of test cases.

```sh
tm add "Plan a weekend trip to Goa with 3 friends" --no-repo --due 2026-11-15 --remind "tomorrow 9am"
tm go 1          # questions, things to consider, and a step-by-step plan
tm done 1.1      # tick off steps as you go
tm today --speak # your day, read out loud
```

### Simple mode

For people who don't write code (or who use TaskMind mostly for life admin):

```sh
tm config --mode simple       # tm setup asks once; switch back with --mode developer
```

- Plain wording: "Task 5 step 2" instead of "TM-5.2", statuses like "new" and "in
  progress" instead of "inbox" and "in_progress".
- New tasks are never linked to git repos.
- Hints point to everyday commands (`tm done`, `tm remind`, `tm today`) instead of
  coding tools. Commands themselves don't change: `tm done 5.2` works in both modes.

## Reminders

Desktop notifications when a task needs you:

| Kind | When |
|---|---|
| Reminder | at the time you set with `--remind` or `tm remind set` |
| Due today | on the due date, at your daily reminder time (default 09:00) |
| Overdue | once a day while a task is past due, saying how many days |

Each notification shows the task's next step, and is sent once.

```sh
tm remind on               # turn on notifications (asks first)
tm remind on --speak       # ...and read them out loud
tm remind set 4 friday 10:30
tm config --remind-time 08:30
tm remind off
```

`tm remind on` adds one line to your crontab so TaskMind checks every 5 minutes,
even with the terminal closed (Linux and macOS). It carries your desktop session
details so notifications reach your screen; your API keys are never written into
the crontab. On Windows, or if you'd rather not use cron, keep `tm remind watch`
running in a terminal.

Notifications use `notify-send` on Linux, Notification Center on macOS and a tray
notification on Windows. Times you type are your local time.

## Calendar

No account or API key needed; works with any calendar app.

```sh
tm calendar export                 # writes ~/.taskmind/taskmind.ics
tm calendar link 4 --open          # opens "add to Google Calendar", prefilled
```

- **Due dates** become all-day events, with an alert at 9:00 that day.
- **Reminders** become 15-minute events with an alert.
- Event details list the open steps.

Import the file once: Google Calendar → Settings → Import & export → Import; Apple
Calendar → File → Import; Outlook → Add calendar → Upload from file. This is an
export, not live sync. After changes, export again: Apple Calendar and Outlook
update events with the same ID, while Google Calendar's import only adds new events,
so for single changes there use `tm calendar link`. Tasks finished in the last 30
days are included as cancelled so calendars that update can drop them.

## Voice

Talk to TaskMind instead of typing, and let it talk back. Free by default.

| Command | What happens |
|---|---|
| `tm talk` | **Hands-free mode.** Press Enter, say what you want ("what should I work on today", "add a task to call the bank tomorrow", "mark task 1 done"), press Enter. TaskMind shows the commands, runs them after you agree, and **answers out loud** ("You have 3 open tasks: ..."). Say "stop" to finish. You can also type a request instead of speaking |
| `tm do --voice` | One spoken request, same as `tm "..."` |
| `tm add --voice` | Say *"add rate limiting to login, high priority, due Friday"*. TaskMind turns it into a task (title, details, due date, priority), shows it, and saves when you confirm |
| `tm go 12 --voice` (or `tm clarify 12 --voice`) | TaskMind reads each clarifying question aloud. Press Enter and answer by speaking, or type the answer, or type `skip` |
| `tm today --speak` | Your day read out loud: overdue, due today, in progress and the next step. With `--ai`, the AI briefing is read instead |
| `tm voice` / `tm voice --test` | Shows what's available; `--test` speaks a sentence and tries your microphone (only a test; it then offers to run what you said as a request) |

**Recording:** press Enter to stop. TaskMind shows what it heard (*You said: "..."*)
before using it. Saying the command name too is fine: "TM list" is understood as "list". Hindi, Hinglish and many other languages are understood.

### What voice needs

| Part | Free option | Notes |
|---|---|---|
| Microphone recording | `arecord` (preinstalled on most Linux), or `sox` / `ffmpeg` | macOS: `brew install sox`. Windows: not supported yet |
| Speech to text | Groq Whisper with your free Groq key | Uses an OpenAI key instead if you have no Groq key |
| Natural voice | Groq Orpheus with your free Groq key | **One-time step:** accept the model's terms at https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english |
| Offline voice (fallback) | `spd-say` (most Linux desktops), macOS `say`, `espeak-ng`, Windows speech | Used automatically when the natural voice isn't available |

If the natural voice fails (terms not accepted yet, limit reached), TaskMind tells
you once and continues with the offline voice. Settings:

```sh
tm config --voice-output auto     # natural voice, else offline voice (default)
tm config --voice-output local    # always the offline voice
tm config --voice-output off      # never speak
tm config --voice-name troy       # autumn (default), diana, hannah, austin, daniel or troy
```

Your recordings are sent to the speech-to-text provider (Groq or OpenAI) and
deleted from your computer right after transcription.

## Working with Claude Code, Cursor and Codex

`tm prompt 3.2` builds a self-contained prompt with the task definition,
acceptance criteria, affected files, edge cases, **security requirements**, the
plan (with the current subtask marked "DO THIS ONE NOW") and test cases. It asks
the coding agent to put `TM-3.2` in its commit messages.

```sh
tm prompt 3.2            # copied to clipboard (prints it if no clipboard tool is found)
tm prompt 3 -o task.md   # the whole task, saved to a file
tm exec 3.2 --tool claude   # starts `claude` in the repo with the prompt
```

`tm exec` supports `claude` (Claude Code), `cursor` (`cursor-agent`) and `codex`.
It asks before starting, because the agent can edit files in your repo.

**Commit references** drive tracking: `TM-3.2` marks subtask 2 of task 3 done,
`TM-3` marks task 3 as started. When every subtask is done, the task is done.

## Security check

Every code task's analysis goes through the same 7 checks:

1. Input validation and injection (SQL, shell, file paths, HTML/templates, CSV/spreadsheet formulas)
2. Authentication and authorization
3. Secrets and credentials
4. Sensitive data exposure (logs, errors, output)
5. Error handling and failure modes
6. Dependencies and external calls
7. Backwards compatibility and data migration

Each is marked ⚠️ risk, ✅ ok or ➖ n/a with a specific note. Something the new code
*must do* to stay safe (check the login, escape output) counts as a risk. If the AI marks
a check "ok" but its note says work is needed ("replicate the same check", "must
escape"), TaskMind upgrades it to a risk itself. Nothing
is silently skipped: if the AI misses a check it shows as ❔ unchecked. Every risk
must become a subtask and a test case, and appears in `tm prompt` as a security
requirement.

## Verify

`tm verify <id>` collects the task's changes and checks every item of the plan
(acceptance criteria, edge cases, security risks, test cases) as ✅ done,
🟡 partial, ❌ missing or ❔ unclear, with evidence. It also reports other problems
it notices (bugs, injection, leftover debug code).

Which changes it looks at:

- default: commits that mention `TM-<id>` (made since the task was created; merge
  commits are skipped), plus uncommitted and new files
- `--base main`: everything on your branch since `main`, plus uncommitted work

Large changes are trimmed to fit the AI's limits, most important files first;
anything left out is listed so you can check it yourself.

```sh
tm verify 12 --strict || echo "TM-12 is not finished"   # exit code 1 on gaps: use in git hooks or CI
```

`--strict` passes only when every item was checked and found done: anything
missing, partial, unclear, or in a file too large to review counts as not done.

When gaps are found, fix them (`tm prompt 12` gives the agent the full picture) and
run `tm verify 12` again. The output says which AI did the review; see
[Independent review](#independent-review).

## AI providers and models

You bring your own key. TaskMind never pays for or routes your AI usage.

| Provider | Cost | Key | Notes |
|---|---|---|---|
| `groq` (default) | free tier | `GROQ_API_KEY` | recommended model `openai/gpt-oss-120b` |
| `gemini` (Google) | free tier | `GEMINI_API_KEY` | |
| `openrouter` | free models available | `OPENROUTER_API_KEY` | pick a model ending in `:free` |
| `ollama` | free, local | none | runs on your machine; slow without a GPU; code never leaves your computer |
| `openai` (ChatGPT) | paid | `OPENAI_API_KEY` | |
| `anthropic` (Claude) | paid | `ANTHROPIC_API_KEY` or `ant auth login` | needs `pip install anthropic`; recommended `claude-opus-5-5` |
| `xai` (Grok) | paid | `XAI_API_KEY` | |

> **Groq vs Grok:** Groq (with a q) is the free provider TaskMind uses by default.
> Grok (with a k) is xAI's paid model.

```sh
tm models                               # what your provider offers right now
tm models --provider openai             # another provider's list
tm config --model openai/gpt-oss-20b    # switch model
tm config --provider gemini             # switch provider (keys and models are kept per provider)
```

Models are always chosen from the provider's live list, because providers retire
models regularly. Keys can come from `tm setup`, `tm config --key`, or the
environment variables above (environment variables win).

On Claude, if Anthropic's safety filter declines a request, the API retries it on
a suitable model automatically (server-side fallback), where the model supports it.

## Never stuck: automatic backups

When a provider fails (daily limit, outage, rejected key, retired model), the stage
that was running continues on the next provider. Finished stages are never redone.

```
⚠ Groq / openai/gpt-oss-120b: Daily free-tier limit reached ...
→ Continuing with Google Gemini / gemini-2.5-flash
```

Order tried for each stage:

1. the stage's own choice, if you set one ([per stage](#a-different-ai-for-each-stage)),
2. your main provider,
3. your backups, in order.

Every provider you set up with `tm setup` is added to the backups automatically.
Change the order with:

```sh
tm config --fallback groq,gemini,anthropic
tm config --fallback groq:openai/gpt-oss-20b   # another model on the same provider (own daily limit)
tm config --fallback ""        # no backups
tm doctor --all                # test all of them
```

`tm show <id>` lists which AI did each stage of a task.

## Independent review

An AI checking its own work misses its own blind spots. `tm verify` therefore
prefers an AI **other than** the one that wrote or planned the code:

```sh
tm config --coder anthropic    # "I write code with Claude Code"
```

With `--review auto` (the default), verify avoids your coder's provider and the
provider that analyzed the task, and uses another one you have set up. The output
says who reviewed:

```
Reviewed by Google Gemini / gemini-2.5-flash
```

If no other provider is set up, it still runs and says it was not an independent
review. Other options:

```sh
tm config --review gemini                  # always this provider (or gemini:<model>)
tm config --review off                     # just use the normal order
tm config --coder none                     # forget the coder setting
```

Typical setup: code with Claude Code, plan with a free model, verify with a
different one. Free combinations work too, for example Groq for planning and
Gemini for reviews.

## A different AI for each stage

Use free models where they are good enough and strong ones where they matter:

```sh
tm config --stage clarify=groq:openai/gpt-oss-20b        # quick questions: small free model
tm config --stage analyze=anthropic:claude-opus-5-5      # deep code analysis: strongest model
tm config --stage verify=gemini                          # reviews: another provider
tm config --stage analyze=                               # clear a stage's choice
```

Stages: `clarify`, `analyze`, `plan`, `verify`, `sync` (`tm sync --ai`) and
`today` (`tm today --ai`). Format: `provider` or `provider:model` (the provider's
saved model is used if you leave the model out). If a stage's choice fails, the
normal order takes over. An explicit `verify` choice overrides the automatic
independent-review choice.

## Free-tier limits and saving tokens

Measured on Groq's free tier, October 2026:

- **8,000 tokens per minute** per model. TaskMind keeps every request under this
  and waits automatically, so a full `tm go` takes about 1–4 minutes. Other
  providers (especially paid ones) usually allow much more per minute; TaskMind
  gives them more room, so they are faster and read more code.
- **200,000 tokens per day** per model. A full `tm go` uses roughly 30–40k tokens
  (about **5 tasks per day per model**); `tm verify` about 5–7k. Each Groq model
  has its own daily allowance, and a backup provider takes over automatically.
- **No AI tokens at all:** `add`, `list`, `show`, `edit`, `prompt`, `exec`,
  `start`, `done`, `sync`, `today`, `import`. Only `sync --ai`, `today --ai` and
  `add --voice` (one small call) use AI.
- **Voice** has its own Groq allowance: speech-to-text allows 2,000 requests per day
  on the free tier (measured October 2026), separate from the chat models.

Check your Groq limits at https://console.groq.com/settings/limits.

## Tips for the best results

- **Run `tm add` inside the repo** so the task is linked to the right code.
- **Write the title as the outcome**, and add context with `-d`: "Let users export
  notes as CSV" with `-d "Excel users, max 10k notes"` beats "CSV".
- **Answer the clarifying questions.** Answers flow into the definition, the plan
  and the checks. Use `--no-questions` only for obvious tasks.
- **Read the security check before coding.** Fix a wrong ✅/⚠️ by editing the task
  description and running `tm go <id> --redo`.
- **One subtask per prompt** (`tm prompt 3.2`) keeps coding agents focused; small
  commits with `TM-3.2` keep tracking automatic.
- **Verify before you merge**, ideally with a second provider set up so the review
  is independent. Add `tm verify <id> --strict` to a pre-push hook or CI.
- **Set up two free providers** (for example Groq and Gemini). You get backups,
  independent reviews and double the daily allowance, all free.
- **Start the day with `tm today`**; it costs nothing. `tm today --speak` reads it
  to you while you get coffee.
- **Talk through hard tasks:** `tm go <id> --voice` answers come out more detailed
  when spoken than typed.

## Configuration reference

Everything lives in `~/.taskmind/` (set `TASKMIND_HOME` to use another folder):

- `config.json`: settings and API keys (readable only by you)
- `taskmind.db`: your tasks (SQLite)

| `config.json` key | Set with | Meaning |
|---|---|---|
| `provider` | `tm setup`, `--provider` | main provider |
| `keys` | `tm setup`, `--key` | API key per provider |
| `models` | `tm setup`, `--model` | chosen model per provider |
| `fallback` | `--fallback` | backup providers, in order |
| `stages` | `--stage` | per-stage `provider[:model]` |
| `coder` | `--coder` | provider whose coding tool you use |
| `review` | `--review` | `auto`, `off` or `provider[:model]` for `tm verify` |
| `voice` | `--voice-output`, `--voice-name` | how TaskMind speaks, and which Groq voice |
| `mode` | `tm setup`, `--mode` | `developer` (default) or `simple` |
| `reminders` | `--remind-time`, `tm remind on --speak` | daily reminder time, and whether reminders are spoken |

Environment variables: `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`,
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY` (override saved keys),
`GITHUB_TOKEN` / `GH_TOKEN` (for `tm import`), `TASKMIND_HOME`.

## Privacy
- **Secrets never reach the AI.** Files that usually hold secrets (`.env`, private keys,
  `credentials*`, `*.pem`, `secrets.*`, `.ssh/`, Terraform state ...) are invisible to the
  agent: never listed, read or searched. Secret-looking values in ordinary files (API
  keys, tokens, passwords, database passwords in URLs, private key blocks) are replaced
  with `[REDACTED]` before any code or diff is sent, including in `tm verify`.
- **You're asked once per project** before any of its code goes to an AI provider, with the
  provider's name, so nothing is sent from a work project without your OK.
- **Your data is safe and yours.** A backup of your task list is made automatically each
  day you use TaskMind (the last 7 are kept in `~/.taskmind/backups`); `tm backup` makes
  one any time, `tm backup --restore FILE` brings one back, and `tm export` gives you
  everything as JSON. Two TaskMind commands running at once (e.g. a reminder check while
  you work) wait for each other instead of failing.

- Tasks, plans and keys stay on your machine.
- During `analyze` and `verify`, the code snippets and diffs being examined are sent
  to the AI provider doing that stage. Use `ollama` to keep everything local.
- The agent's code tools are **read-only** and cannot leave the repo folder.
- `tm exec` asks before starting a coding agent, which can edit files.
- Voice recordings go to the speech-to-text provider and are deleted locally right
  after transcription. TaskMind only records between your key presses.
- Reminders and calendar export run entirely on your machine. `tm remind on` adds
  one tagged line to your crontab (`tm remind off` removes it) and never writes
  API keys there.

## Troubleshooting

| Message | What to do |
|---|---|
| "needs an API key" / "isn't connected to an AI yet" | Run `tm setup` |
| "Daily free-tier limit reached" | Wait, switch model (`tm models`, `tm config --model ...`), or set up a backup provider: it then takes over automatically |
| "Model ... is not available" | The provider retired it: `tm models`, then `tm config --model <id>` |
| "API key was rejected" | Create a new key and run `tm setup` again |
| "Claude needs Anthropic's official SDK" | `pip install anthropic` |
| "No changes found for TM-n" | Put `TM-n.<subtask>` in commit messages, keep work uncommitted, or use `tm verify n --base main` |
| "has no plan to check against" | Run `tm go <id>` first |
| Analysis seems off | Add detail with `tm edit <id> -d "..."`, then `tm go <id> --redo` |
| Every provider failed | `tm doctor --all` shows which one is broken and why |
| Not sure what something means | `tm ask "..."` in your own words, or `tm help <topic>` |
| "Something went wrong inside TaskMind" | That's a bug: run `tm report-bug` and send the file it saves. Your tasks are safe (daily backups in `~/.taskmind/backups`) |
| `SyntaxError` or `tm` won't start | You need Python 3.9 or newer: `python3 --version` |
| "needs a one-time terms acceptance" | Accept the Orpheus terms at the link shown (free, once). Until then the offline voice is used |
| "No audio recorder found" | Linux: `sudo apt install alsa-utils`. macOS: `brew install sox` |
| "Nothing was recorded" / "didn't catch anything" | Check the microphone isn't muted (`tm voice --test`), speak closer, press Enter only after speaking |
| No notifications | `tm remind` shows if they're on. Test with `tm remind set <id> +0m` then `tm remind check`. On Linux, `notify-send` comes with `libnotify-bin` |
| Notifications stopped after logging in again | Run `tm remind on` once more so cron gets your new desktop session |
| Reminder at the wrong time | Times are your computer's local time; check `date`. Daily time: `tm config --remind-time` |
| No sound at all | `tm voice` shows the speaking engine; on Linux install `speech-dispatcher` or `espeak-ng` for the offline voice |

## Development

```sh
python3 -m unittest discover -s tests     # 253 unit tests, no AI calls, no dependencies
python3 tests/live_smoke.py               # every command end to end, with your real AI provider
python3 tests/live_smoke.py --no-ai       # the same, skipping AI calls
```

For a guided manual test of every feature, see [TESTING.md](TESTING.md). What's planned
next is in [ROADMAP.md](ROADMAP.md).

Before a release: `python3 scripts/build.py` (refreshes the bundled guide and builds
`dist/taskmind.pyz`). `.github/workflows/ci.yml` runs the tests on Linux, macOS and Windows
with Python 3.9 to 3.13 on every push, once the project is on GitHub. The code is split
by topic: `cli.py` (commands and the AI flow), `core.py`, `parsing.py`, `display.py`,
`personal.py`, `planner.py`, plus one module per feature (`stages.py`, `verify.py`, ...).

The unit tests replace the AI with scripted fakes. The live smoke test uses a
temporary data folder and a temporary git project (your tasks are untouched),
costs roughly 60-90k tokens, and may show one desktop notification.

## License

MIT © 2026 Kartik Trivedi. Free to use, change and share; see [LICENSE](LICENSE).
