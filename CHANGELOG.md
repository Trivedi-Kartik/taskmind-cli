# What's new in TaskMind

Update with `tm update`. Your tasks, settings and keys are kept.

## 0.9.0 (2026-10-19)

- **Quick mode, the new default for `tm build` and the session.** TaskMind finds the code itself and the AI
  answers once with the edits, instead of searching step by step. On a real task: 5 AI requests and 8 seconds
  instead of 25 to 44 steps and 4 to 10 minutes. That is what makes free tiers usable: a small task no longer
  uses up the day's quota. It hands over to the step-by-step mode, and says why, when a task doesn't suit it.
  `tm build --agent` forces the step-by-step mode.
- **Every build reports its AI requests and tokens** ("Quick mode: 5 AI requests, about 22k tokens").
- Small projects are shown to the AI in full; bigger ones as a map plus the files and functions the request
  concerns, and the AI can ask for more (a function, an outline, a line range, a text search).

## 0.8.0 (2026-10-19)

- **A session, like Claude Code, Codex and Gemini.** Just type `tm` in a terminal: a prompt where you say
  what you want built, ask questions about the code, and follow up without repeating yourself ("make
  that bigger"). Passed builds are kept straight away; `/undo` takes them back, `/diff` shows what changed.
  Ctrl+C stops a build and returns to the prompt. `/help` lists everything; any `tm` command still works inside.
- Questions about the code are answered from the code (with the index and your project notes) and change nothing.
- `/new FOLDER request` starts a new project and the session moves into it.
- Fixed: a command was refused as "turns the computer off" only because a script contained the word
  `shutdown` (`server.shutdown()`); now only a command that actually is one is blocked. Multi-line check
  commands print on one line, and the daily greeting no longer interrupts a session.

## 0.7.0 (2026-10-19)

- **The code index for big projects.** TaskMind keeps a map of your code (definitions with line
  numbers, who imports what) and every build starts with it: the project map, the files your request
  probably concerns, and the code of the best-matching functions. `tm index` shows it;
  `--find` and `--outline` look things up. Measured on one task: 25 steps instead of 44, finished
  instead of running out of steps.
- **Fixed: big projects were partly invisible.** Only the first 5,000 files were listed; in a
  12,000-file repository whole folders could not be seen or searched. Every file is listed now,
  and searches over huge projects stop after 8 seconds and say so.
- When the AI runs out of steps, TaskMind now runs the project's own checks and tells you whether
  the work so far passes, instead of only saying "not finished". Providers with room get 60 steps (was 40).

## 0.6.0 (2026-10-19)

- **Follow-ups:** `tm build --more "make the button bigger"` continues from the last build and
  remembers every earlier round, so "make *that* bigger" works.
- **Pick up where it stopped:** `tm build --more` with no text continues a build that failed its
  checks, was interrupted, or hit an AI limit, on the same branch.

## 0.5.0 (2026-10-19)

- **Project notes:** every build first reads `TASKMIND.md`, `CLAUDE.md`, `AGENTS.md`,
  `.cursorrules` and Copilot instructions, so projects set up for Claude Code, Codex or Cursor
  work with TaskMind straight away.
- **`tm init`** studies a project and writes `TASKMIND.md` (stack, layout, how to run and test,
  conventions) for you to check and share in git.
- **Point at files with @:** `tm build "add the checks from @src/login.ts to @src/signup.ts"`
  starts the AI from exactly those files. Typos are caught before the build starts.

## 0.4.0 (2026-10-17)

- **Web pages are checked in a real browser.** After a build, TaskMind opens the page in a
  hidden Chrome or Edge: JavaScript errors, missing files or a blank page go back to the AI
  to fix. A test that only checks "the files exist" no longer earns ✅.
- **`tm build --undo`** takes back the last build. No git knowledge needed: unsaved changes
  and leftover builds are explained, with an offer to sort them out for you.
- **`tm update`** installs the newest version, and TaskMind tells you once a day when one is out.
- **Gemini works properly:** bigger reads and replies, waits when the per-minute limit is
  reached instead of giving up, and explains daily limits with the time until they reset.
- Model names as websites show them work: `tm config --model "Gemini 3.1 Flash Lite"`.
- Fixed: a crash loop on unquoted words, a list number saved as a model name, files built in
  a needless subfolder, empty AI replies counted as "finished", support for Python 3.9.

## 0.3.0 (2026-10-17)

- **`tm build`: TaskMind does the work.** It writes the code and tests, runs them, fixes what
  fails and saves the result on a safe branch; Enter keeps it.
- `tm build --new FOLDER` starts a new project; summaries in plain words, Hindi or Hinglish.
- Outside a project, it builds in a new folder instead of touching what's there.

## 0.2.0 (2026-10-16)

- Planning (`tm go`), independent review (`tm verify`), natural-language commands
  (`tm "..."`), voice, reminders, calendar, automatic failover across AI providers.
