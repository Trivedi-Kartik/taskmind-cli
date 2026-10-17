# TaskMind manual test guide

Work through the sections in order. Each test has an **ID**, the **steps**, and what
you should **expect**. If anything is different (an error, a crash, confusing
wording, a wrong or useless answer), note the ID and report it; see
[How to report a problem](#how-to-report-a-problem).

Task numbers below (TM-1, TM-2 ...) assume you follow the order from a fresh start;
if yours differ, use the number `tm add` printed.

Time needed: about 2–3 hours for everything. Sections are independent after
section 0, so you can spread them over several days.

---

## Start here: the tutorial (10 minutes)

If you haven't yet, run `tm tutorial` first (after `alias tm=...` below). It walks you
through one task on a practice project, explaining every command before and after it
runs, so the tests below make sense. Typing just `tm` shows a short overview any time.

## 0. Before you start (5 minutes)

```sh
cd /home/user/kartik/folder
alias tm="$PWD/tm"                                # for this terminal (add to ~/.bashrc to keep it)
tm --version                                      # expect: taskmind 0.1.0
tm config --fallback groq:openai/gpt-oss-20b      # backup model in case the daily limit runs out
```

Create a small sample project for the coding tests:

```sh
mkdir -p ~/tm-sample && cd ~/tm-sample && git init -q
cat > app.py <<'EOF'
import sqlite3

conn = sqlite3.connect("notes.db")


def notes_for(user_id):
    return conn.execute("SELECT id, title, body FROM notes WHERE user_id = ?", (user_id,)).fetchall()


def handle_notes(request):
    if not request.user_id:
        return 401, {"error": "login required"}
    return 200, [{"id": n[0], "title": n[1], "body": n[2]} for n in notes_for(request.user_id)]
EOF
git add . && git commit -qm "Notes app" && echo ready
```

> Free-tier note: Groq allows about 5 full `tm go` runs per model per day. The backup
> model above takes over automatically when the main one runs out.

---

## A. Setup and AI connection

| ID | Steps | Expect |
|---|---|---|
| A1 | `tm --help` | A list of commands including setup, go, verify, remind, calendar, voice |
| A2 | `tm setup` → press Enter for Groq → press Enter to keep your key → press Enter for the recommended model | Live model list with "← recommended", "Connected", "Saved". The first time it asks "Do you write code?": answer **y** |
| A3 | `tm doctor` then `tm doctor --all` | `OK Groq / openai/gpt-oss-120b: TaskMind is ready`, and one line per backup |
| A4 | `tm models` | The current Groq chat models, with your model marked "← current" |
| A5 | `tm config` | A table: provider, model, api key **set** (the key itself is never shown), backups `groq:openai/gpt-oss-20b`, mode developer |
| A6 (optional) | Get a free Gemini key at https://aistudio.google.com/apikey, run `tm setup`, choose 2 | Gemini is added; `tm config` shows it in the backups; `tm doctor --all` checks both |

## B. Developer flow (in `~/tm-sample`)

| ID | Steps | Expect |
|---|---|---|
| B1 | `cd ~/tm-sample` then `tm add "Let users download their notes as a CSV file" -p high --due friday` | `Added TM-1 (repo: .../tm-sample)` |
| B2 | `tm list` then `tm show 1` | TM-1 with status inbox, priority high, Friday's date |
| B3 | `tm go 1` and **answer** the questions (type answers, press Enter to skip one) | Three clearly labelled stages: **Step 1 of 3 · Understanding your task** (questions, then "The task, written down clearly" and "Done when"), **Step 2 of 3 · Reading your code** (lines like `→ read_file app.py`, then files, edge cases, risks and the **Security check**), **Step 3 of 3 · Making a plan** (TM-1.1, TM-1.2 ... and test cases). Ends with "✅ TM-1 is planned: ..." and "What to do next". Takes 1–4 minutes |
| B4 | Read the security check in B3's output | **Authentication** should be ⚠️ risk (the export must check the login). Input/CSV injection should be a risk too. Report it if they're marked ok |
| B5 | `tm show 1` | Everything from B3, plus "AI used: clarify → groq/..., analyze → ..." |
| B6 | `tm prompt 1.1` | Copied to the clipboard, or printed if there's no clipboard tool. Contains "DO THIS ONE NOW", "Security requirements" and "Put `TM-1.1` in each commit message" |
| B7 | `tm prompt 1 -o ~/tm-prompt.md` then `cat ~/tm-prompt.md` | The whole task written to the file |
| B8 (optional) | `tm exec 1.1 --tool claude` | Asks "Continue?"; on **y**, Claude Code starts in the repo with the prompt |
| B9 | Write any small change (or let Claude Code do it), then `git add . && git commit -m "Add CSV export TM-1.1"`, then `tm sync` | `TM-1.1 done (...)` and `TM-1 started` |
| B10 | `tm verify 1` | "Verification" with ✅/🟡/❌/❔ per item, evidence, and "Reviewed by ..." |
| B11 | **Planted-bug test:** add `def export(user_id): return conn.execute(f"SELECT * FROM notes WHERE user_id = {user_id}").fetchall()` and `print("DEBUG", user_id)` to app.py, commit with `TM-1.2`, then `tm verify 1` | Reports the **SQL injection** and the **debug print** under "Other issues", and the missing login check |
| B12 | `tm verify 1 --strict; echo "exit code $?"` | `exit code 1` while anything is missing |
| B13 | `tm done 1.3`, then `tm done 1.3 --undo`, then `tm done 1.99` | Done ✅, then reopened, then `Error: TM-1 has no step 99` |
| B14 | `tm edit 1 --title "Export notes as CSV" --due +5d` then `tm show 1` | New title and date |
| B15 | `tm sync --ai` | Runs without errors (it may find no matches) |

## C. Choosing AI per step, failover, independent review

| ID | Steps | Expect |
|---|---|---|
| C1 | In `~/tm-sample`: `tm add "Add a notes count to the API"` (TM-2) then `tm config --stage clarify=groq:openai/gpt-oss-20b` then `tm go 2 --no-questions` then `tm show 2` | "AI used: clarify → groq/openai/gpt-oss-20b, analyze → groq/openai/gpt-oss-120b ..." |
| C2 | **Failover:** `tm config --stage analyze=groq:llama-3.3-70b-versatile` (a retired model) then `tm analyze 2` | `⚠ Groq / llama-3.3-70b-versatile: Model ... is not available` then `→ Continuing with ...`, and the analysis completes |
| C3 | Clean up: `tm config --stage analyze= --stage clarify=` | Saved; "per-stage AI: none" |
| C4 | `tm config --coder anthropic` then `tm verify 1` | "Not an independent review: no AI other than ..." (expected with only Groq set up). With Gemini from A6: "Reviewed by Google Gemini" |
| C5 | `tm config --coder none` | Saved |

## D. Everyday tasks and simple mode

| ID | Steps | Expect |
|---|---|---|
| D1 | (TM-3) `tm add "Plan a weekend trip to Goa with 3 friends in December" --no-repo --due 2026-11-30` then `tm go 3` and answer the questions | Practical questions (dates, budget, people). Plain-language "things to consider", steps you can do in one sitting, "Done when" instead of test cases, and no security check |
| D2 | Judge D1's plan: would it actually help you plan the trip? | Report anything useless, wrong or missing; that's valuable feedback |
| D3 | `tm config --mode simple` then `tm list` then `tm show 3` | "Task 3", "Task 3 step 1", statuses like "new" and "planned"; no "TM-3" |
| D4 | Still in simple mode: `cd ~/tm-sample && tm add "Buy groceries"` (Task 4) | Added Task 4 **without** "repo:" |
| D5 | `tm config --mode developer` | Back to TM-3 wording |

## E. Reminders

| ID | Steps | Expect |
|---|---|---|
| E1 | (TM-5) `tm add "Call the bank" --no-repo --remind +2m` | "🔔 Reminder set for ..." with a hint to turn notifications on |
| E2 | `tm remind` | A table with your reminders and due dates; "Notifications: off" |
| E3 | Wait 2 minutes, then `tm remind check` | A **desktop notification** "Reminder: Call the bank", and the same line in the terminal |
| E4 | `tm remind check` again | "No reminders due right now" (each reminder is sent once) |
| E5 | `tm remind set 5 friday 10:30`, `tm remind set 5 tonight 9`, `tm remind set 5 someday` | Friday's date at 10:30; today at **21:00**; an error for "someday" |
| E6 | `tm add "Pay electricity bill" --no-repo --due 2026-10-10` then (after 09:00) `tm remind check` | Notification "Overdue by N days: Pay electricity bill" |
| E7 | `tm remind on` → **y**, then `crontab -l` | "Reminders are on", a test notification, and one crontab line ending in `# taskmind-reminders` |
| E8 | `tm remind set 5 +1m`, close the terminal, wait up to 6 minutes | The notification appears by itself (the check runs every 5 minutes) |
| E9 | `tm remind off` then `crontab -l` | "Reminders are off"; the TaskMind line is gone |
| E10 | `tm config --remind-time 8:30am` then `tm remind` | "Due-date reminders at 08:30" |

## F. Calendar

| ID | Steps | Expect |
|---|---|---|
| F1 | `tm calendar export` | "Exported N event(s) ... to ~/.taskmind/taskmind.ics" with import instructions |
| F2 | Import that file into Google Calendar (Settings → Import & export → Import) | Due dates as all-day events ("Due: ..."), reminders as 15-minute events, with the open steps in the details |
| F3 | `tm calendar link 3 --open` | Your browser opens Google Calendar's "new event" page, already filled in |
| F4 | `tm add "No dates here" --no-repo` then `tm calendar link <its id>` | A clear error: no due date or reminder |

## G. Voice

| ID | Steps | Expect |
|---|---|---|
| G1 | `tm voice` | microphone recorder: arecord, speech to text: Groq / whisper-large-v3-turbo, text to speech: Groq Orpheus |
| G2 | `tm voice --test` → y → say a sentence → press Enter | A spoken test sentence (offline voice until G3), "You said: ..." with your words, then it repeats them |
| G3 | Accept the Orpheus terms (free, once): https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english, then `tm voice --test` again | A natural voice instead of the robotic one |
| G4 | `tm add --voice --no-repo`, say: *"Book a dentist appointment, high priority, due Friday, remind me tomorrow at 10"* → y | Title, priority high, Friday's date, reminder tomorrow 10:00; saved after you confirm |
| G5 | Repeat G4 in **Hindi or Hinglish**, e.g. *"Kal subah 9 baje mummy ko call karna hai, urgent"* | A sensible task with a reminder tomorrow at 09:00 |
| G6 | `tm add "Organise team lunch" --no-repo` then `tm go <id> --voice`: answer the 1st question by voice (press Enter, speak, Enter), type the 2nd, type `skip` for the 3rd | Questions are read aloud; "You said: ..." after speaking; the plan uses your answers |
| G7 | `tm today --speak` | Your day read out loud ("Good morning ... Due today: ...") |
| G8 | `tm config --voice-name troy` then `tm voice --test`; then `tm config --voice-output off` then `tm today --speak` | A different voice; then silence. Put it back with `tm config --voice-output auto` |

## H. Daily view

| ID | Steps | Expect |
|---|---|---|
| H1 | `tm today` | Sections: overdue, due today, in progress (with the next step), ready to start, needs thinking, finished in the last 24h |
| H2 | `tm today --ai` | The same, plus a short AI "Briefing" |

## I. Errors and edge cases (try to break it)

| ID | Steps | Expect |
|---|---|---|
| I1 | `tm show 999`, `tm add`, `tm edit 1 --due someday`, `tm done 1.` | A short, clear `Error: ...` each time, never a long Python "Traceback" |
| I2 | `tm list \| head -1` | One line, no error |
| I3 | `cd ~/tm-sample && tm add "Add note search"`, then `tm go <its id> --no-questions` and press **Ctrl+C** once "Analyzing" starts; then run `tm go <its id> --no-questions` again | "Cancelled.", then the second run skips the finished definition and continues with the analysis |
| I4 | `cd /tmp && tm add "Not in a repo"` | Added without "repo:" |
| I5 | `tm add "fix [/] parser [bold]x[/bold]" --no-repo` then `tm list` | The title shows exactly as typed |
| I6 | `tm import octocat/Hello-World --limit 3` then `tm list`, then the same import again | "Imported N new issue(s)"; the second time it skips them |
| I7 | Anything else you'd naturally try | No crash. Report anything confusing |

## K. Your own steps

| ID | Steps | Expect |
|---|---|---|
| K1 | `tm add "Plan my week" --no-repo` then `tm step add <id> "Review calendar"` and `tm step add <id> "Block focus time" -d "2 hours each morning"` | "Added step TM-n.1", then TM-n.2; the task shows as planned |
| K2 | `tm step edit <id>.2 "Block deep work time"` then `tm step list <id>` | The new title, and the details kept |
| K3 | `tm step rm <id>.1` (answer y), then `tm step add <id> "Plan meals"` | The new step is **TM-n.3**, not 1: numbers are never reused |
| K4 | `tm step rm <id>.9`, `tm step edit <id>`, `tm step add <id>` | Clear errors: no step 9 / say which step / give a title |

## L. Getting help

| ID | Steps | Expect |
|---|---|---|
| L1 | `tm` | The short "to-do list that thinks" overview |
| L2 | `tm ask "I am confused, how do I use this?"` | A short, beginner-friendly answer with real commands |
| L3 | `tm ask "what is left to do on task 1?"` | An answer about **your** task 1 (its steps and the next one) |
| L4 | `tm ask "reminder kaise set karu? 2 ghante baad chahiye"` | An answer in Hinglish with `tm remind set <id> +2h` |
| L5 | `tm how do I add a task` (no quotes, no `ask`) | Treated as a question and answered |
| L6 | `tm help`, then `tm help reminders`, then `tm help voice` | The topic list, then the matching guide sections (works offline) |
| L7 | `tm lst`, `tm go`, `tm go abc`, `tm remind sett` | Plain-language errors with suggestions ("Did you mean `tm list`?"), never "usage:" noise |
| L8 | **Broken key, safely:** `GROQ_API_KEY=wrong-key tm ask "my key is not working"` (this uses a wrong key for this one command only; your saved key is untouched) | Steps to fix the key, plus the matching guide section, instead of a failure |

## M. Just say what you want

| ID | Steps | Expect |
|---|---|---|
| M1 | `tm "remind me about the CSV task tomorrow at 9 and make it high priority"` → Enter | "I'll run:" with `tm edit 1 -p high` and `tm remind set 1 tomorrow 9am`, then both run |
| M2 | `tm "add three tasks: buy milk, call mom, and pay rent by friday"` | Three `tm add` commands, the rent one with `--due friday` |
| M3 | `tm "kal subah 10 baje doctor appointment ka reminder laga do"` | A Hinglish message; a new task plus a reminder tomorrow 10:00 |
| M4 | `tm "what should I work on today?"` | Runs `tm today` straight away (no question, since it only shows things) |
| M5 | `tm "delete the call mom task"` → press **Enter** | Marked "⚠ deletes", the question is `[y/N]`, and Enter changes nothing |
| M6 | `tm "email my boss the report"` then `tm wishlist` | An honest "can't do that", and the request appears in the wishlist |
| M7 | `tm "mark the task done"` (with several tasks) | It asks which task you mean; answer, then it proposes `tm done <n>` |
| M8 | `tm do --dry-run "set all my tasks to high priority"` | Shows the plan and says nothing was changed |
| M9 | `tm do --voice` and say "add a task to buy flowers" | You said: "...", then the plan |
| M10 | `tm "change my api key to abc"` | Refused: settings are never changed for you |

## N. Fixes from the first test session

| ID | Steps | Expect |
|---|---|---|
| N1 | `tm tutorial`, and at "Ready to start?" type something that isn't y/n (e.g. `tm add "x"`) | "Please answer y (yes) or n (no)..." and it asks again; the tutorial does not quit |
| N2 | `tm og 1` | Instantly "Did you mean `tm go 1`? [Y/n]" (no AI call, no second question) |
| N3 | `tm step 1.3`, then `tm step 1` | Step 1.3 in full (done or not, details, how to tick it off); then the task's steps |
| N4 | `tm voice --test`, say "TM list" | Explains it was only a test and offers to run it; say y: `tm list` runs |
| N5 | `tm talk`, press Enter, say "what is on my list", press Enter | Runs `tm list` and **says** "You have N open tasks: ..." out loud |
| N6 | In `tm talk`: say "add a task to call the bank tomorrow" → Enter to agree | The task is added and it says "Done." |
| N7 | In `tm talk`: type a request instead of speaking, then say or type "stop" | Typed requests work too; "stop" ends with "Okay, bye!" |

## O. Secrets, data safety, and getting to know you

| ID | Steps | Expect |
|---|---|---|
| O1 | In `~/tm-sample`: `printf 'STRIPE_KEY=sk_%s\n' live_51Habcdefghijklmnopqrstuv > .env` (a fake key, split so this guide doesn't contain one) and add `API_KEY = "gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCD"` to app.py; then `tm add "Use the API key" ` and `tm go <id> --no-questions` | The analysis never shows `.env`, and app.py's key appears only as `[REDACTED]` (the AI never received it) |
| O2 | `ls ~/.taskmind/backups` | Today's automatic backup is there |
| O3 | `tm backup`, then `tm rm 1 -y`, then `tm backup --restore <the file it printed>` → y | Task 1 is back |
| O4 | `tm export -o ~/tasks.json` | A JSON file with all your tasks, steps and activity |
| O5 | `tm me` and answer the questions | Saved; `tm today` shows your name |
| O6 | Tomorrow, the first `tm` command you run | "Good morning, <name>! ..." with your day and a tip (once per day) |
| O7 | `tm today` | A 💡 Suggestions section (e.g. an overdue task, a task never thought through) |
| O8 | `tm week` | What you finished, steps done, and what's slipping |
| O9 | `tm greet --install`, log out and in again | A desktop notification with your day about 10 seconds after login; undo with `tm greet --uninstall` |

## P. Phase 1: install, safety and learning

| ID | Steps | Expect |
|---|---|---|
| P1 | `python3 scripts/build.py`, then `python3 dist/taskmind.pyz --version` and `python3 dist/taskmind.pyz help reminders` | taskmind 0.2.0, and the help works from the single file |
| P2 (optional) | If you have pipx: `pipx install /home/user/kartik/folder` then `tm --version` in a new terminal | The `tm` command works from anywhere |
| P3 | In a new project folder (any `git init` folder with a file), `tm add "x"` then `tm go <id>` | First it asks "Allow this for this project?", naming your AI provider; answer **n**: "nothing was sent". Run again and answer y: it doesn't ask a third time |
| P4 | `tm report-bug "testing the report"` | A saved report file; open it: version, system, provider name, recent errors, and **no API key** |
| P5 | `tm feedback "the plans are great"` | "Thank you!" and where it was saved |
| P6 | Remove a step from an AI plan (`tm step rm <id>.2`) and add one (`tm step add <id> "Write tests"`) on two tasks, then `tm me` | "Learned from your plan edits: ..." mentions "Write tests"; the next `tm go` says "Planned the way you like" |

## J. Automatic checks (optional, any time)

```sh
cd /home/user/kartik/folder
python3 -m unittest discover -s tests     # expect: OK (123+ tests)
python3 tests/live_smoke.py               # expect: 50/50 passed (uses ~60-90k tokens)
```

---

## When you're done

- `tm remind off` (if you turned it on), and `rm -rf ~/tm-sample` if you no longer need it.
- Your tasks stay in `~/.taskmind/taskmind.db`. To start fresh, delete that file (the config and key stay).

## How to report a problem

Copy this and fill it in:

```
Test ID:        (e.g. B11)
Command:        (exactly what you typed)
What happened:  (paste the full output, including any error)
What I expected:
Notes:          (anything else, e.g. "the plan suggested X, which is wrong because ...")
```

Helpful extras: the output of `tm --version` and `tm config` (it never shows your
API key, so it's safe to paste). **Never paste your API key itself.**

Feedback that isn't a bug is just as useful: confusing wording, a step that felt
slow or annoying, a plan that wasn't helpful, or a feature you missed.
