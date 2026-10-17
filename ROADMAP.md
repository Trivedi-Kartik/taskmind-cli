# TaskMind roadmap

Where TaskMind stands, and what it takes to go from "works well for me" to "a product
other people rely on". Updated 2026-10-17.

## Where it stands today: a strong alpha

**Ready:** the full task flow (capture → clarify → analyze code → plan → hand off → track →
verify), security check, independent review, automatic failover across 7 AI providers,
natural-language requests (`tm "..."`), voice (`tm talk`), reminders, calendar, help that
works offline, secret protection, daily backups, profile, greeting and suggestions.
220 automated tests (on Python 3.9–3.13), a live end-to-end test, and an independent code review behind it.

**Not yet ready for strangers:** it's installed by cloning a folder, it has only been
used on small demo projects and one Linux laptop, and nobody but its creator has used it.
The AI's judgment varies between runs, and we don't yet *measure* how good its plans are.

| Who | Ready? | What's missing |
|---|---|---|
| You | Yes | - |
| A developer teammate | Almost | Easy install, testing on their OS, a way to send feedback (Phase 1) |
| The public | Not yet | Quality measured on real projects, Mac/Windows, performance on large repos, a home page (Phase 2) |
| Non-technical relatives | No | They won't use a terminal: needs an app, web or WhatsApp interface (Phase 3) |

## Phase 1: ready for teammates

- [x] Secrets never sent to the AI (secret files hidden, values redacted)
- [x] Data safety: daily backups, restore, export, safe concurrent use
- [x] Getting to know the user: profile, daily greeting, suggestions, weekly summary
- [x] **Easy install:** `pipx install taskmind-cli` (the name `taskmind` is taken on PyPI), or the single file `taskmind.pyz`; tested with a real install in a clean container
- [x] **Works on Python 3.9 to 3.13** (it didn't start on 3.9–3.11 before; found and fixed by testing each version)
- [ ] Publish to PyPI (needs your PyPI account) and check for updates in `tm --version`
- [x] **Automatic tests on Linux, macOS and Windows** ready in `.github/workflows/ci.yml` (starts running once the project is on GitHub)
- [x] **`tm feedback` / `tm report-bug`**, and a friendly message (with the details saved) instead of a Python crash
- [x] `tm report-bug` and `tm feedback` open prefilled issues on github.com/Trivedi-Kartik/taskmind-cli
- [x] **Clearer consent:** asked once per project, naming the AI provider
- [x] **Split `cli.py`** into topic modules (core, parsing, display, personal, planner); checked with pyflakes
- [x] Learn from your edits: plans the way you like (fewer steps, steps you always add)
- [x] On GitHub: github.com/Trivedi-Kartik/taskmind-cli (tests run on every push)
- [ ] Give it to 2–3 developer teammates

## Phase 2: public beta

- [ ] **Measure quality:** a test set of 20–30 real tasks on real open-source projects, with the expected files and risks, scored automatically, so every change and every model can be compared on facts instead of impressions
- [ ] **Large projects:** a cached code index for fast search in big repos (thousands of files)
- [ ] **Speed:** measure the time for each command; reuse work across runs
- [ ] macOS and Windows fully supported (recording, notifications, reminders)
- [x] A LICENSE (MIT) and a public GitHub repo
- [ ] A simple home page and a short demo video
- [ ] Release notes and versioning

## Phase 3: product

- [ ] Optional sync across devices (the first feature that needs an account; it could be the paid plan)
- [ ] Team features: shared tasks, assigning work, a team view
- [ ] A non-developer interface: mobile, web or WhatsApp, on the same engine
- [ ] Integrations: GitHub/GitLab issues both ways, Jira/Linear, Slack, live Google Calendar sync

## Principles that stay

- Free to run: users bring their own AI keys; nothing paid is required.
- Local and private by default: tasks, keys and history stay on the user's computer.
- The AI proposes, the code does: anything that must always be correct is plain code.
- The process, not the model, sets the quality bar.
