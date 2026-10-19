# TaskMind roadmap

Updated 2026-10-17.

## The direction

TaskMind **builds the task**, and proves it works. One engine (`tm build`) serves two kinds
of people:

- **Teams and developers:** a task goes in, and a tested change comes out, saved on a safe branch.
  It works with free models or any AI you have, with TaskMind's own test run as the
  judge of "done".
- **People who can't code (India first):** describe the app in Hindi, Hinglish or English,
  and get a working program, explained in plain words.

What's measured from now on is **whether real tasks get done**, graded by checks the AI never sees
(the scoreboard below), not only how many unit tests pass.

## Step 1: the engine (now)

- [x] `tm build`: writes and edits files, runs commands, writes tests, fixes failures
- [x] Safe by design: a work branch, risky commands need a yes, dangerous ones never run,
      keys hidden from commands, secret files never committed, uncommitted work never mixed in
- [x] Done means proven: TaskMind runs the check itself; up to 3 fix rounds; a warning when
      existing tests were changed; the AI can't swap in an easier check
- [x] One command: Enter keeps it (merge + tidy up); planned tasks tick their steps off
- [x] New projects (`--new`), plain-language and Hinglish summaries, voice input
- [x] First honest scoreboard on real tasks (see README)
- [ ] Scoreboard to 20 tasks across Python, JavaScript, web pages, small bug fixes in real
      open-source projects; the same tasks run with Claude Code for comparison
- [ ] Faster on free tiers: send less per step, and reuse what was read

## Step 1b: working in real projects (agreed 2026-10-19)

- [x] **1. Project notes:** builds read TASKMIND.md, CLAUDE.md, AGENTS.md, .cursorrules,
      Copilot instructions; `tm init` writes TASKMIND.md
- [x] **2. @mentions:** `tm build "... @src/app.ts ..."` starts from those files; typos caught
- [ ] **3. Follow-ups:** `tm build --more "make it bigger"` continues the last build (same
      branch, remembers what it did); later `tm chat`, a back-and-forth session where every
      change is still tested and undoable. Also resume a build interrupted by Ctrl+C or limits
- [ ] **4. Big projects:** a saved index of the codebase (files, symbols, what imports what),
      refreshed on change, so the AI finds things in one step instead of ten

**Next ideas, found while testing (after 4):**

- [ ] **Honest summaries:** compare what the AI *says* it changed with what it *did* (seen live:
      "updated script.js" when only test.html changed) and correct the summary
- [ ] **See the changes before keeping:** a `d` answer at "Keep it?" shows the diff, file by file
- [ ] **A second AI reviews every build** before "Keep it?" (the independent review `tm verify`
      already does, applied to the diff), catching what tests miss
- [ ] **What it cost:** requests, tokens and time per build, and how much of today's free quota is left
- [ ] **Undo also resets the task** (seen: undone builds stay "in progress" in `tm list`)
- [ ] **Pictures as input:** "make it look like this screenshot"
- [ ] **See it running:** a local preview link for web builds; for non-coders, publish to a free
      host (e.g. GitHub Pages) and get a link to share
- [ ] **Overnight queue:** `tm build --all` works through the task list, each on its own branch,
      with a morning report
- [ ] **Knowing who uses it:** opt-in, anonymous usage counts (nothing about code or tasks)

## Step 2: real users (the next 4 weeks)

- [ ] 5 developers from different small teams use `tm build` on real work for a week. Measure:
      how many results they kept
- [ ] 5 people who don't code try `tm build --new` with someone watching. Measure: did they get
      a program they use?
- [ ] Fix whatever stops them. Decide the main audience from what they *do*, not what they say

## Step 3: the front doors

- [ ] **For non-coders:** they won't open a terminal. A simple web page or WhatsApp chat on the
      same engine, with the built app running somewhere they can open (a link)
- [ ] **For teams:** GitHub issue in → pull request out (`tm build` on an issue, then a PR
      with the test results), so the whole team can use it without installing anything
- [x] `tm update`, a daily "new version" note and CHANGELOG.md
- [ ] Publish to PyPI (`pipx install taskmind-cli`), a short demo video

## Already done (foundations)

Planning (`tm go`: questions, code reading, security check, steps), independent review
(`tm verify`), automatic failover across 7 AI providers, natural-language commands,
voice, reminders, calendar, secret protection, daily backups, profile, tests on
Linux, macOS and Windows with Python 3.9 to 3.13, MIT license.

## Principles that stay

- Free to run: users bring their own AI keys; nothing paid is required.
- Local and private by default: tasks, keys and history stay on the user's computer.
- The AI proposes, the code enforces: safety rules, git and the final check are plain code.
- Judged by results: a feature counts when real tasks get done with it.
