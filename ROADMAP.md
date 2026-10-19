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
