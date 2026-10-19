# What's new in TaskMind

Update with `tm update`. Your tasks, settings and keys are kept.

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
