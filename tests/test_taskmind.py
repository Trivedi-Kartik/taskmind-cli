"""Run with: python3 -m unittest discover -s tests"""

import contextlib
import gc
import io
import json
import shutil
import sqlite3
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from datetime import date, datetime

from taskmind import cli, personal, stages, tracking
from taskmind import parsing as parsing_module
from taskmind.agent import TRIMMED, _compact, run_tool_agent
from taskmind.db import DB
from taskmind.db import close_all as close_all_dbs
from taskmind.core import remove_tree


class TempDir(tempfile.TemporaryDirectory):
    """A temporary folder that closes TaskMind's databases before deleting itself.
    Windows can't delete files that are still open."""

    def cleanup(self):
        close_all_dbs()
        gc.collect()
        try:
            super().cleanup()
        except PermissionError:
            if os.name != "nt":
                raise
            # Something still holds a database open: close every connection still alive,
            # report it (so leaks in TaskMind itself get noticed), and clean up.
            leaked = [o for o in gc.get_objects() if isinstance(o, sqlite3.Connection)]
            for conn in leaked:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::warning title=open sqlite connections::{len(leaked)} still open at cleanup")
            remove_tree(self.name)
from taskmind.llm import RequestTooLarge, ToolCallError, _retry_delay, extract_json
from taskmind.repo_tools import Repo, dispatch


class FakeLLM:
    """Returns scripted assistant messages in order and records every request."""

    model = "fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None, json_mode=False, **kw):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "json_mode": json_mode, "kw": kw})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict) and "tool_calls" in reply:
            return reply
        return {"role": "assistant", "content": reply if isinstance(reply, str) else json.dumps(reply)}


def tool_call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}


ANALYSIS = {
    "summary": "Add rate limiting to login.",
    "affected_files": [{"path": "app/auth.py", "reason": "login handler"}, "app/config.py"],
    "edge_cases": ["Many requests from one IP", ""],
    "risks": ["Locking out real users"],
}
PLAN = {
    "priority": "HIGH",
    "subtasks": [
        {"title": "Add limiter", "details": "Token bucket", "files": ["app/auth.py"], "estimate_hours": "2"},
        {"title": "Write tests", "estimate_hours": 1},
        {"details": "no title, should be dropped"},
    ],
    "tests": ["6th attempt in a minute returns 429"],
}


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def make_repo(root):
    root = Path(root)
    (root / "app").mkdir()
    (root / "app" / "auth.py").write_text("def login(user, password):\n    return check(user, password)\n")
    (root / "app" / "config.py").write_text("RATE = 5\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("login()\n")
    (root / ".gitignore").write_text("node_modules/\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


class TestJsonAndRetry(unittest.TestCase):
    def test_extract_json_variants(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('```json\n{"a": 2}\n```'), {"a": 2})
        self.assertEqual(extract_json('Sure! Here it is: {"a": 3} hope it helps'), {"a": 3})
        with self.assertRaises(ValueError):
            extract_json("no json here")

    def test_daily_limit_fails_fast(self):
        import urllib.error
        from taskmind.llm import LLM, LLMError
        body = io.BytesIO(b'{"error":{"message":"Rate limit reached on tokens per day (TPD): Limit 200000. '
                          b'Please try again in 5m53.3s."}}')
        err = urllib.error.HTTPError("u", 429, "x", {}, body)
        with mock.patch("urllib.request.urlopen", side_effect=err) as urlopen, mock.patch("time.sleep") as sleep:
            with self.assertRaises(LLMError) as ctx:
                LLM("http://x", "m").chat([{"role": "user", "content": "hi"}])
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("Daily free-tier limit", str(ctx.exception))
        self.assertIn("5m53.3s", str(ctx.exception))

    def test_retry_delay(self):
        self.assertEqual(_retry_delay("3", "", 0), 3.0)
        self.assertEqual(_retry_delay(None, "Please try again in 1m2.5s.", 0), 60)
        self.assertEqual(_retry_delay(None, "Please try again in 7.5s.", 0), 8.0)
        self.assertEqual(_retry_delay(None, "", 1), 4)


class TestDB(unittest.TestCase):
    def setUp(self):
        self.db = DB(":memory:")

    def test_task_lifecycle(self):
        tid = self.db.add_task("Fix login", priority="high", source="github", source_ref="o/r#1")
        self.db.update_task(tid, analysis={"summary": "x"}, status="analyzed")
        task = self.db.get_task(tid)
        self.assertEqual(task["analysis"], {"summary": "x"})
        self.assertEqual(self.db.find_by_source("github", "o/r#1")["id"], tid)
        self.db.set_subtasks(tid, [{"title": "a"}, {"title": "b", "files": ["x.py"]}])
        self.assertTrue(self.db.mark_subtask(tid, 2, True, "abc"))
        self.assertFalse(self.db.mark_subtask(tid, 2, True, "abc"))  # no change the second time
        self.assertEqual(self.db.subtasks(tid)[1]["files"], ["x.py"])
        with self.assertRaises(ValueError):
            self.db.update_task(tid, bogus=1)
        self.db.delete_task(tid)
        self.assertIsNone(self.db.get_task(tid))
        self.assertEqual(self.db.subtasks(tid), [])

    def test_list_ordering_and_done_filter(self):
        low = self.db.add_task("low", priority="low")
        high = self.db.add_task("high", priority="high")
        done = self.db.add_task("done")
        self.db.update_task(done, status="done")
        self.assertEqual([t["id"] for t in self.db.list_tasks()], [high, low])
        self.assertEqual(len(self.db.list_tasks(include_done=True)), 3)


class TestRepoTools(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.repo = Repo(make_repo(self.tmp.name))

    def tearDown(self):
        close_all_dbs()
        self.tmp.cleanup()

    def test_files_respect_gitignore(self):
        self.assertIn("app/auth.py", self.repo.files())
        self.assertNotIn("node_modules/junk.js", self.repo.files())

    def test_search_and_read(self):
        self.assertIn("app/auth.py:1:", self.repo.search("def login"))
        self.assertIn("No matches", self.repo.search("nothing_like_this"))
        self.assertIn("    1 def login", self.repo.read("app/auth.py"))

    def test_path_traversal_blocked(self):
        self.assertIn("outside the repo", dispatch(self.repo, "read_file", {"path": "../../etc/passwd"}))

    def test_read_with_end_before_start(self):
        self.assertIn("lines 2-2", self.repo.read("app/auth.py", 2, 1))

    def test_tool_argument_aliases(self):
        out = dispatch(self.repo, "read_file", {"file_path": "app/auth.py", "line_start": 2, "line_end": 2})
        self.assertIn("lines 2-2", out)
        self.assertIn("app/config.py", dispatch(self.repo, "search_code", {"query": "RATE", "path": "app"}))
        self.assertIn("No matches", dispatch(self.repo, "search_code", {"pattern": "RATE", "path": "node_modules"}))

    def test_bad_tool_input_does_not_crash(self):
        self.assertIn("Invalid regex", dispatch(self.repo, "search_code", {"pattern": "("}))
        self.assertIn("Tool error", dispatch(self.repo, "read_file", {}))
        self.assertIn("Unknown tool", dispatch(self.repo, "rm_rf", {}))

    def test_log(self):
        self.assertEqual(self.repo.log(5)[0]["subject"], "initial")


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.repo = Repo(make_repo(self.tmp.name))
        self.task = {"id": 1, "title": "Rate limit login", "description": ""}

    def tearDown(self):
        close_all_dbs()
        self.tmp.cleanup()

    def test_analyze_with_tools(self):
        llm = FakeLLM(tool_call("search_code", pattern="login"), tool_call("read_file", path="app/auth.py"),
                      "```json\n" + json.dumps(ANALYSIS) + "\n```")
        steps = []
        result = stages.analyze(llm, self.task, self.repo, on_step=lambda n, a: steps.append(n))
        self.assertEqual(steps, ["search_code", "read_file"])
        self.assertEqual(result["affected_files"][1], {"path": "app/config.py", "reason": ""})
        self.assertEqual(result["edge_cases"], ["Many requests from one IP"])
        tool_msgs = [m for m in llm.calls[-1]["messages"] if m["role"] == "tool"]
        self.assertIn("def login", tool_msgs[-1]["content"])

    def test_analyze_falls_back_when_tool_calling_breaks(self):
        llm = FakeLLM(ToolCallError("tool_use_failed"), ToolCallError("tool_use_failed"), ANALYSIS)
        result = stages.analyze(llm, self.task, self.repo)
        self.assertEqual(result["summary"], "Add rate limiting to login.")
        self.assertTrue(llm.calls[-1]["json_mode"])
        self.assertIn("No tools are available; use this repository snapshot", llm.calls[-1]["messages"][-1]["content"])

    def test_analyze_without_repo(self):
        llm = FakeLLM({"summary": "Book flights", "affected_files": ["x"], "edge_cases": ["visa"]})
        result = stages.analyze(llm, {"id": 2, "title": "Plan trip"}, None)
        self.assertEqual(result["edge_cases"], ["visa"])
        self.assertIsNone(llm.calls[0]["tools"])

    def test_step_limit_writes_answer_from_findings(self):
        llm = FakeLLM(tool_call("list_files"), tool_call("read_file", path="app/auth.py"),
                      tool_call("search_code", pattern="RATE"), json.dumps(ANALYSIS))
        text = run_tool_agent(llm, "sys", "user", self.repo, max_steps=3)
        self.assertEqual(json.loads(text)["summary"], ANALYSIS["summary"])
        final = llm.calls[-1]
        self.assertIsNone(final["tools"])
        self.assertTrue(final["json_mode"])
        self.assertIn("def login", final["messages"][-1]["content"])
        self.assertIn("RATE = 5", final["messages"][-1]["content"])

    def test_write_up_step_is_not_told_to_use_tools(self):
        llm = FakeLLM(tool_call("list_files"), json.dumps(ANALYSIS))
        stages.analyze(llm, {"id": 1, "title": "Rate limit login"}, self.repo)
        self.assertIn("Investigate with your tools", llm.calls[0]["messages"][1]["content"])
        llm = FakeLLM(*[tool_call("list_files", path=str(i)) for i in range(10)], json.dumps(ANALYSIS))
        stages.analyze(llm, {"id": 1, "title": "Rate limit login"}, self.repo)
        self.assertNotIn("with your tools", llm.calls[-1]["messages"][-1]["content"])

    def test_tool_call_in_no_tools_step_is_retried(self):
        llm = FakeLLM(ToolCallError("Tool choice is none, but model called a tool"), {"questions": ["q?"]})
        self.assertEqual(stages.clarify_questions(llm, {"id": 1, "title": "t"}), ["q?"])
        self.assertIn("You have no tools", llm.calls[-1]["messages"][-1]["content"])

    def test_broken_tool_calling_after_progress_still_answers(self):
        llm = FakeLLM(tool_call("read_file", path="app/auth.py"), ToolCallError("x"), ToolCallError("x"),
                      json.dumps(ANALYSIS))
        text = run_tool_agent(llm, "sys", "user", self.repo)
        self.assertIn("def login", llm.calls[-1]["messages"][-1]["content"])
        self.assertEqual(json.loads(text)["summary"], ANALYSIS["summary"])

    def test_unparseable_direct_answer_uses_findings_not_snapshot(self):
        llm = FakeLLM(tool_call("read_file", path="app/auth.py"), "Here is my analysis: looks fine!",
                      json.dumps(ANALYSIS))
        steps = []
        result = stages.analyze(llm, {"id": 1, "title": "Rate limit login"}, self.repo,
                                on_step=lambda n, a: steps.append(n))
        self.assertNotIn("fallback", steps)
        self.assertEqual(result["summary"], ANALYSIS["summary"])
        self.assertIn("def login", llm.calls[-1]["messages"][-1]["content"])

    def test_calls_left_hint(self):
        llm = FakeLLM(tool_call("list_files"), json.dumps(ANALYSIS))
        run_tool_agent(llm, "sys", "user", self.repo, max_steps=5)
        self.assertIn("[4 tool calls left.]", llm.calls[-1]["messages"][-1]["content"])

    def test_shrinks_conversation_when_request_too_large(self):
        llm = FakeLLM(tool_call("read_file", path="app/auth.py"), RequestTooLarge("413"), json.dumps(ANALYSIS))
        text = run_tool_agent(llm, "sys", "user", self.repo, budget=8000)
        self.assertEqual(json.loads(text)["summary"], ANALYSIS["summary"])
        self.assertEqual(len(llm.calls), 3)

    def test_garbled_tool_call_is_retried_once(self):
        llm = FakeLLM(ToolCallError("output_parse_failed"), json.dumps(ANALYSIS))
        self.assertIn("summary", run_tool_agent(llm, "sys", "user", self.repo))
        llm = FakeLLM(ToolCallError("x"), ToolCallError("x"))
        with self.assertRaises(ToolCallError):
            run_tool_agent(llm, "sys", "user", self.repo)

    def test_repeated_identical_call_is_not_rerun(self):
        llm = FakeLLM(tool_call("read_file", path="app/auth.py"), tool_call("read_file", path="app/auth.py"),
                      json.dumps(ANALYSIS))
        steps = []
        run_tool_agent(llm, "sys", "user", self.repo, on_step=lambda n, a: steps.append(n))
        self.assertEqual(steps, ["read_file"])
        self.assertIn("already made this exact call", llm.calls[-1]["messages"][-1]["content"])

    def test_compact_trims_oldest_tool_output(self):
        msgs = [{"role": "user", "content": "x"}, {"role": "tool", "content": "a" * 100},
                {"role": "tool", "content": "b" * 100}]
        _compact(msgs, 150)
        self.assertEqual(msgs[1]["content"], TRIMMED)
        self.assertEqual(msgs[2]["content"], "b" * 100)


class TestPlanAndHandoff(unittest.TestCase):
    def test_plan_normalizes(self):
        result = stages.plan(FakeLLM(PLAN), {"id": 1, "title": "t"}, ANALYSIS)
        self.assertEqual(result["priority"], "high")
        self.assertEqual(len(result["subtasks"]), 2)
        self.assertEqual(result["subtasks"][0]["estimate_hours"], 2.0)
        self.assertEqual(result["estimate_hours"], 3.0)

    def test_redo_definition_ignores_previous_one(self):
        llm = FakeLLM({"definition": "new", "acceptance_criteria": []})
        stages.write_definition(llm, {"id": 1, "title": "t", "definition": "OLD GO STUFF", "acceptance": ["x"]})
        self.assertNotIn("OLD GO STUFF", llm.calls[0]["messages"][-1]["content"])

    def test_handoff_prompt_focuses_subtask(self):
        task = {"id": 7, "title": "Rate limit", "definition": "Limit logins", "acceptance": ["429 after 5"],
                "analysis": stages._normalize_analysis(ANALYSIS), "tests": ["t1"]}
        subs = [{"position": 1, "title": "Add limiter", "details": "Token bucket", "done": 1},
                {"position": 2, "title": "Write tests", "details": "Cover IPs", "done": 0}]
        text = stages.handoff_prompt(task, subs, position=2)
        self.assertIn("- [x] TM-7.1 Add limiter", text)
        self.assertIn("TM-7.2 Write tests  <-- DO THIS ONE NOW", text)
        self.assertIn("Cover IPs", text)
        self.assertNotIn("Token bucket", text)
        self.assertIn("`TM-7.2` in each commit message", text)


class TestTracking(unittest.TestCase):
    def test_refs(self):
        self.assertEqual(list(tracking.refs_in("fix TM-3.2 and tm-4, not XTM-9")), [(3, 2), (4, None)])

    def test_sync_marks_subtasks_from_commits(self):
        with TempDir() as tmp:
            root = make_repo(tmp)
            db = DB(":memory:")
            tid = db.add_task("Rate limit", repo=str(root))
            db.set_subtasks(tid, [{"title": "a"}, {"title": "b"}])
            # Created-at must be before the commits; git's --since has 1s resolution.
            db.conn.execute("UPDATE tasks SET created_at = '2000-01-01T00:00:00+00:00'")
            (root / "a.txt").write_text("1")
            git(root, "add", ".")
            git(root, "commit", "-qm", f"Add limiter TM-{tid}.1")
            changes = tracking.sync_task(db, db.get_task(tid))
            self.assertTrue(any(f"TM-{tid}.1 done" in c for c in changes))
            self.assertEqual(db.get_task(tid)["status"], "in_progress")
            self.assertEqual(tracking.sync_task(db, db.get_task(tid)), [])  # idempotent
            (root / "b.txt").write_text("2")
            git(root, "add", ".")
            git(root, "commit", "-qm", "tests", "-m", f"Refs TM-{tid}.2")
            tracking.sync_task(db, db.get_task(tid))
            self.assertEqual(db.get_task(tid)["status"], "done")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.env = mock.patch.dict(os.environ, {"TASKMIND_HOME": self.tmp.name, "GROQ_API_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        close_all_dbs()
        self.tmp.cleanup()

    def run_cli(self, *argv, llm=None, stdin=""):
        buf = io.StringIO()
        patches = [mock.patch("sys.stdin", io.StringIO(stdin))]
        if llm:
            fake = {"provider": "groq", "label": "Fake", "model": "fake-model"}
            patches += [mock.patch.object(cli, "make_llm", return_value=llm),
                        mock.patch.object(cli, "ensure_setup", return_value={}),
                        mock.patch.object(cli.routing, "candidates", return_value=[fake]),
                        mock.patch.object(cli.routing, "review_candidates", return_value=([fake], None)),
                        mock.patch.object(cli.routing, "from_settings", return_value=llm)]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(cli, "copy_to_clipboard", return_value=False))
            stack.enter_context(contextlib.redirect_stdout(buf))
            stack.enter_context(contextlib.redirect_stderr(buf))
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_missing_key_message(self):
        code, out = self.run_cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("tm setup", out)
        self.assertIn("console.groq.com", out)

    def test_full_flow(self):
        root = Path(self.tmp.name) / "proj"
        root.mkdir()
        make_repo(root)
        code, out = self.run_cli("add", "Rate limit login", "--repo", str(root), "--due", "+2d", "-p", "low")
        self.assertEqual(code, 0, out)
        self.assertIn("TM-1", out)

        llm = FakeLLM(
            {"questions": ["Per IP or per user?"]},
            {"definition": "Limit **per IP**.", "acceptance_criteria": ["429 after 5 tries"]},
            tool_call("search_code", pattern="login"),
            json.dumps(ANALYSIS),
            PLAN,
        )
        code, out = self.run_cli("go", "1", llm=llm, stdin="per IP\n")
        self.assertEqual(code, 0, out)
        self.assertIn("Per IP or per user?", out)
        self.assertIn("app/auth.py", out)
        self.assertIn("TM-1.2", out)
        self.assertEqual(llm.replies, [])
        definition_request = llm.calls[1]["messages"][-1]["content"]
        self.assertIn("A: per IP", definition_request)

        code, out = self.run_cli("prompt", "1.1")
        self.assertIn("DO THIS ONE NOW", out)

        code, out = self.run_cli("list")
        self.assertIn("planned", out)
        self.assertIn("0/2", out)

        self.run_cli("done", "1.1")
        code, out = self.run_cli("done", "1.2")
        self.assertIn("complete", out)
        code, out = self.run_cli("today")
        self.assertIn("Finished in the last 24h", out)

        code, out = self.run_cli("show", "1")
        self.assertIn("429 after 5 tries", out)

        code, out = self.run_cli("done", "1.5")
        self.assertEqual(code, 1)
        self.assertIn("has no step 5", out)
        code, out = self.run_cli("done", "1.")
        self.assertEqual(code, 1)
        code, out = self.run_cli("show", "99")
        self.assertEqual(code, 1)

    def test_due_weekday_today_means_today(self):
        friday = date(2026, 10, 16)
        with mock.patch.object(parsing_module, "date", wraps=date) as d:
            d.today.return_value = friday
            self.assertEqual(cli.parse_due("friday"), "2026-10-16")
            self.assertEqual(cli.parse_due("monday"), "2026-10-19")

    def test_due_parsing(self):
        self.assertEqual(len(cli.parse_due("tomorrow")), 10)
        with self.assertRaises(cli.CLIError):
            cli.parse_due("next week")
        self.assertEqual(cli.parse_ref("TM-12.3"), (12, 3))
        self.assertEqual(cli.parse_ref("12"), (12, None))


if __name__ == "__main__":
    unittest.main()


class TestSecurityChecklist(unittest.TestCase):
    def test_normalize_fills_every_check(self):
        result = stages._normalize_security({"1": {"status": "RISK", "note": "SQL built by hand"},
                                             "3": {"status": "weird"}})
        self.assertEqual(len(result), len(stages.SECURITY_CHECKS))
        self.assertEqual(result[0]["status"], "risk")
        self.assertEqual(result[1]["status"], "unchecked")
        self.assertEqual(result[2]["status"], "unchecked")
        as_list = stages._normalize_security([{"status": "ok"}, {"status": "n/a"}])
        self.assertEqual([s["status"] for s in as_list[:3]], ["ok", "n/a", "unchecked"])

    def test_ok_that_needs_work_becomes_risk(self):
        got = stages._normalize_security({
            "2": {"status": "ok", "note": "Authentication already checked in the handler; replicate same check."},
            "3": {"status": "ok", "note": "No new secrets are introduced."},
            "4": {"status": "ok", "note": "You must not log note bodies."}})
        self.assertEqual([g["status"] for g in got[1:4]], ["risk", "ok", "risk"])
        self.assertIn("has to do this", got[1]["note"])

    def test_analysis_always_has_security_even_if_model_skips_it(self):
        with TempDir() as tmp:
            repo = Repo(make_repo(tmp))
            llm = FakeLLM(json.dumps(ANALYSIS))  # no "security" key in the reply
            result = stages.analyze(llm, {"id": 1, "title": "Rate limit login"}, repo)
            prompt = llm.calls[0]["messages"][1]["content"]
        self.assertIn("Authentication and authorization", prompt)
        self.assertIn('"security"', prompt)
        self.assertEqual({s["status"] for s in result["security"]}, {"unchecked"})

    def test_non_code_tasks_have_no_security_section(self):
        result = stages.analyze(FakeLLM({"summary": "trip"}), {"id": 2, "title": "Plan trip"}, None)
        self.assertEqual(result["security"], [])

    def test_everyday_tasks_get_a_plain_language_planner(self):
        llm = FakeLLM({"questions": []}, {"summary": "Trip", "edge_cases": ["Passport expiry"]},
                      {"priority": "high", "subtasks": [{"title": "Book train tickets", "estimate_hours": 0.5}],
                       "tests": ["Tickets in email"]})
        task = {"id": 1, "title": "Plan Goa trip"}
        stages.clarify_questions(llm, task)
        stages.analyze(llm, task, None)
        result = stages.plan(llm, task, {"summary": "Trip"})
        self.assertTrue(all(c["messages"][0]["content"] == stages.GENERAL_PERSONA for c in llm.calls))
        self.assertIn("what people usually forget", llm.calls[1]["messages"][-1]["content"])
        self.assertIn("single sitting", llm.calls[2]["messages"][-1]["content"])
        self.assertNotIn("committable", llm.calls[2]["messages"][-1]["content"])
        self.assertEqual(result["subtasks"][0]["title"], "Book train tickets")
        code_llm = FakeLLM({"questions": []})
        stages.clarify_questions(code_llm, {"id": 2, "title": "x", "repo": "/code"})
        self.assertEqual(code_llm.calls[0]["messages"][0]["content"], stages.PERSONA)

    def test_plan_and_handoff_carry_security(self):
        analysis = {**stages._normalize_analysis(ANALYSIS),
                    "security": stages._normalize_security({"1": {"status": "risk", "note": "Validate IP header"}})}
        llm = FakeLLM(PLAN)
        stages.plan(llm, {"id": 1, "title": "t", "repo": "/code"}, analysis)
        self.assertIn('status \\"risk\\" must be handled', json.dumps(llm.calls[0]["messages"][-1]["content"]))
        text = stages.handoff_prompt({"id": 1, "title": "t", "analysis": analysis}, [])
        self.assertIn("## Security requirements", text)
        self.assertIn("Validate IP header", text)
        self.assertIn("not assessed yet", text)

    def test_db_migration_adds_verification_column(self):
        with TempDir() as tmp:
            path = Path(tmp) / "old.db"
            import sqlite3
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                         "description TEXT NOT NULL DEFAULT '', definition TEXT NOT NULL DEFAULT '', "
                         "acceptance TEXT NOT NULL DEFAULT '[]', clarifications TEXT NOT NULL DEFAULT '[]', "
                         "analysis TEXT NOT NULL DEFAULT '{}', tests TEXT NOT NULL DEFAULT '[]', "
                         "status TEXT NOT NULL DEFAULT 'inbox', priority TEXT NOT NULL DEFAULT 'medium', "
                         "estimate_hours REAL, due TEXT, repo TEXT, source TEXT NOT NULL DEFAULT 'manual', "
                         "source_ref TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("INSERT INTO tasks (title, created_at, updated_at) VALUES ('old', 'x', 'x')")
            conn.commit()
            conn.close()
            db = DB(path)
            self.assertEqual(db.get_task(1)["verification"], {})
            db.update_task(1, verification={"counts": {}})
            self.assertEqual(db.get_task(1)["verification"], {"counts": {}})


VERIFY_TASK = {
    "id": 1, "title": "Rate limit login", "acceptance": ["429 after 5 tries"],
    "analysis": {"edge_cases": ["Many IPs"], "affected_files": [{"path": "app/auth.py", "reason": ""}],
                 "security": [{"check": "Input validation", "status": "risk", "note": "X-Forwarded-For spoofing"},
                              {"check": "Secrets", "status": "ok", "note": ""}]},
    "tests": ["6th attempt returns 429"],
}


class TestVerify(unittest.TestCase):
    def setUp(self):
        from taskmind import verify
        self.verify = verify
        self.tmp = TempDir()
        self.root = make_repo(self.tmp.name)
        self.repo = Repo(self.root)

    def tearDown(self):
        close_all_dbs()
        self.tmp.cleanup()

    def commit(self, path, text, message):
        (self.root / path).write_text(text)
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", message)

    def test_checklist_ids(self):
        ids = [(i["id"], i["kind"]) for i in self.verify.checklist(VERIFY_TASK)]
        self.assertEqual(ids, [("A1", "acceptance"), ("E1", "edge case"), ("S1", "security"), ("T1", "test")])

    def test_collects_only_this_tasks_commits_plus_uncommitted(self):
        self.commit("app/auth.py", "def login(): limit()\n", "Add limiter TM-1.1")
        self.commit("app/other.py", "x = 11\n", "Unrelated TM-11 work")
        self.commit("app/config.py", "RATE = 9\n", "Tune TM-1")
        (self.root / "app" / "new_test.py").write_text("def test_limit(): pass\n")  # untracked
        changes = self.verify.collect_changes(self.repo, 1)
        files = self.verify.split_files(changes["diff"])
        self.assertEqual(set(files), {"app/auth.py", "app/config.py", "app/new_test.py"})
        self.assertIn("uncommitted", changes["source"])
        self.assertEqual(len(changes["commits"]), 2)

    def test_base_mode_and_nothing_to_verify(self):
        with self.assertRaises(self.verify.NothingToVerify):
            self.verify.collect_changes(self.repo, 1)
        git(self.root, "branch", "main-copy")
        self.commit("app/auth.py", "def login(): limit()\n", "no ref in message")
        changes = self.verify.collect_changes(self.repo, 1, base="main-copy")
        self.assertIn("app/auth.py", self.verify.split_files(changes["diff"]))

    def test_fit_diff_prioritizes_planned_files(self):
        files = {"z_big.py": "x" * 5000, "app/auth.py": "a" * 100, "tests/test_a.py": "t" * 100,
                 "huge.py": "h" * 9000}
        text, omitted = self.verify.fit_diff(files, ["app/auth.py"], 5300)
        self.assertTrue(text.startswith("a" * 100))
        self.assertIn("t" * 100, text)
        self.assertIn("truncated", text)
        self.assertEqual(omitted, ["z_big.py"])

    def test_run_normalizes_verdicts(self):
        self.commit("app/auth.py", "def login(): limit()\n", "Add limiter TM-1.1")
        changes = self.verify.collect_changes(self.repo, 1)
        llm = FakeLLM({"summary": "Mostly done", "items": [
            {"id": "[A1]", "status": "DONE", "evidence": "auth.py returns 429"},
            {"id": "S1", "status": "missing", "evidence": "trusts X-Forwarded-For"},
            {"id": "T1", "status": "bogus"}], "other_issues": ["debug print left in"]})
        result = self.verify.run(llm, VERIFY_TASK, [], changes)
        status = {i["id"]: i["status"] for i in result["items"]}
        self.assertEqual(status, {"A1": "done", "E1": "unclear", "S1": "missing", "T1": "unclear"})
        self.assertEqual(result["counts"], {"done": 1, "partial": 0, "missing": 1, "unclear": 2})
        self.assertEqual(result["other_issues"], ["debug print left in"])
        prompt = llm.calls[0]["messages"][-1]["content"]
        self.assertIn("[S1] (security) Input validation: X-Forwarded-For spoofing", prompt)
        self.assertIn("+def login(): limit()", prompt)

    def test_run_without_plan_refuses(self):
        with self.assertRaises(self.verify.NothingToVerify):
            self.verify.run(FakeLLM(), {"id": 1, "title": "t"}, [], {"diff": "x", "commits": [], "source": ""})


class TestVerifyCLI(TestCLI):
    def test_verify_command(self):
        root = Path(self.tmp.name) / "proj"
        root.mkdir()
        make_repo(root)
        self.run_cli("add", "Rate limit login", "--repo", str(root))
        db = DB(Path(self.tmp.name) / "taskmind.db")
        db.update_task(1, acceptance=VERIFY_TASK["acceptance"], analysis=VERIFY_TASK["analysis"],
                       tests=VERIFY_TASK["tests"])
        code, out = self.run_cli("verify", "1")
        self.assertEqual(code, 1)
        self.assertIn("No changes found for TM-1", out)

        (root / "app" / "auth.py").write_text("def login(): limit()\n")  # uncommitted work counts
        verdict = {"summary": "Gaps remain", "items": [{"id": "A1", "status": "done", "evidence": "ok"},
                                                       {"id": "S1", "status": "missing", "evidence": "no"}]}
        code, out = self.run_cli("verify", "1", llm=FakeLLM(verdict))
        self.assertEqual(code, 0, out)
        self.assertIn("1 missing", out)
        self.assertIn("Reviewed by Fake / fake-model", out)
        self.assertIn("X-Forwarded-For", out)
        code, out = self.run_cli("verify", "1", "--strict", llm=FakeLLM(verdict))
        self.assertEqual(code, 1)
        code, out = self.run_cli("show", "1")
        self.assertIn("Verification", out)
        self.assertIn("Security check", out)


class TestProviders(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.env = mock.patch.dict(os.environ, {"TASKMIND_HOME": self.tmp.name, "GROQ_API_KEY": "",
                                                "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        close_all_dbs()
        self.tmp.cleanup()

    def test_old_single_key_config_is_migrated(self):
        from taskmind import config
        config.config_path().write_text(json.dumps({"provider": "groq", "api_key": "gsk_old", "model": "m1"}))
        cfg = config.load()
        self.assertEqual(cfg["keys"], {"groq": "gsk_old"})
        self.assertEqual(cfg["models"], {"groq": "m1"})
        self.assertNotIn("api_key", cfg)

    @unittest.skipIf(os.name == "nt", "POSIX file permissions")
    def test_keys_and_models_are_kept_per_provider(self):
        from taskmind import config
        code, _ = TestCLI.run_cli(self, "config", "--key", "gsk_1", "--model", "openai/gpt-oss-20b")
        code, _ = TestCLI.run_cli(self, "config", "--provider", "openai", "--key", "sk-2", "--model", "gpt-x")
        cfg = config.load()
        self.assertEqual(cfg["keys"], {"groq": "gsk_1", "openai": "sk-2"})
        TestCLI.run_cli(self, "config", "--provider", "groq")
        s = config.llm_settings(config.load())
        self.assertEqual((s["api_key"], s["model"]), ("gsk_1", "openai/gpt-oss-20b"))
        self.assertEqual(oct(config.config_path().stat().st_mode & 0o777), "0o600")

    def test_openai_request_shape(self):
        from taskmind import config, llm
        s = {**config.llm_settings({"provider": "openai", "keys": {"openai": "k"}, "models": {"openai": "gpt-x"}})}
        client = llm.from_settings(s)
        sent = {}

        def fake(path, body=None):
            sent.update(body)
            return {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        client._request = fake
        client.chat([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y", "_raw": [1]}],
                    max_tokens=500)
        self.assertNotIn("temperature", sent)
        self.assertNotIn("max_tokens", sent)
        self.assertEqual(sent["max_completion_tokens"], 8000)  # floor for reasoning models
        self.assertNotIn("_raw", sent["messages"][1])

    def test_groq_keeps_small_replies_and_temperature(self):
        from taskmind import config, llm
        client = llm.from_settings(config.llm_settings({"provider": "groq", "keys": {"groq": "k"}, "models": {}}))
        self.assertEqual((client.min_reply_tokens, client.send_temperature, client.context_budget), (0, True, 9000))

    def test_missing_model_is_a_clear_error(self):
        from taskmind import config, llm
        with self.assertRaises(llm.LLMError) as ctx:
            llm.from_settings(config.llm_settings({"provider": "openai", "keys": {}, "models": {}}))
        self.assertIn("tm setup", str(ctx.exception))

    def test_model_list_filters_non_chat(self):
        from taskmind import cli
        self.assertEqual(cli.chat_models(["gpt-x", "text-embedding-3", "whisper-1", "tts-1", "llama-guard"]),
                         ["gpt-x"])

    def test_setup_wizard(self):
        from taskmind import cli, config

        class Probe:
            model = "gemini-pro-x"

            def list_models(self):
                return ["embedding-001", "gemini-2.5-flash", "gemini-pro-x"]

            def chat(self, messages, **kw):
                return {"content": "TaskMind is ready"}
        inputs = iter(["2", "2", "n", "Kartik"])  # Gemini, 2nd model, "Do you write code?" no, name
        with mock.patch.object(cli.ui, "ask", side_effect=lambda p: next(inputs)), \
                mock.patch.object(cli.ui, "secret", return_value="AIza-secret"), \
                mock.patch.object(cli.llm_client, "from_settings", return_value=Probe()), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            cli.run_setup()
        cfg = config.load()
        self.assertEqual(cfg["provider"], "gemini")
        self.assertEqual(cfg["models"]["gemini"], "gemini-pro-x")
        self.assertEqual(cfg["keys"]["gemini"], "AIza-secret")
        self.assertEqual(cfg["fallback"], ["gemini"])  # set-up providers become backups
        self.assertEqual(cfg["mode"], "simple")
        self.assertEqual(cfg["profile"]["name"], "Kartik")
        self.assertIn("recommended", out.getvalue())  # gemini-2.5-flash is the suggestion
        self.assertNotIn("AIza-secret", out.getvalue())


class FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeAnthropicModule:
    """Stands in for the `anthropic` SDK package (not installed in CI)."""

    class AnthropicError(Exception):
        pass

    class APIStatusError(AnthropicError):
        def __init__(self, message, status_code=400):
            super().__init__(message)
            self.message, self.status_code = message, status_code

    class AuthenticationError(APIStatusError): pass
    class PermissionDeniedError(APIStatusError): pass
    class NotFoundError(APIStatusError): pass
    class RateLimitError(APIStatusError): pass
    class BadRequestError(APIStatusError): pass
    class APIConnectionError(AnthropicError): pass

    def __init__(self, replies):
        module = self
        self.calls = []

        class Messages:
            def __init__(self, beta):
                self.beta = beta

            def create(self, **kw):
                module.calls.append({"beta": self.beta, **kw})
                reply = module.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply

        class Client:
            def __init__(self, **kw):
                module.client_kwargs = kw
                self.messages = Messages(False)
                self.beta = FakeBlock(messages=Messages(True))
                self.models = FakeBlock(list=lambda: [FakeBlock(id="claude-opus-5-5"), FakeBlock(id="claude-haiku-5-5")])

        self.Anthropic = Client
        self.replies = list(replies)


def claude_reply(*blocks, stop="end_turn"):
    return FakeBlock(content=list(blocks), stop_reason=stop, stop_details=None)


class TestClaude(unittest.TestCase):
    def make(self, *replies, model="claude-opus-5-5"):
        fake = FakeAnthropicModule(replies)
        patcher = mock.patch.dict("sys.modules", {"anthropic": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        from taskmind.claude import ClaudeLLM
        return ClaudeLLM(model, "sk-ant-test"), fake

    def test_missing_sdk_has_install_hint(self):
        from taskmind import claude
        from taskmind.llm import LLMError
        with mock.patch.dict("sys.modules", {"anthropic": None}):
            with self.assertRaises(LLMError) as ctx:
                claude.ClaudeLLM("claude-opus-5-5")
        self.assertIn("pip install anthropic", str(ctx.exception))

    def test_message_translation(self):
        from taskmind.claude import to_claude
        system, msgs = to_claude([
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "goal"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
                {"id": "t2", "function": {"name": "list_files", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "code"},
            {"role": "tool", "tool_call_id": "t2", "content": "files"},
            {"role": "assistant", "content": "x", "_raw": ["RAW BLOCKS"]},
        ])
        self.assertEqual(system, "persona")
        self.assertEqual(msgs[1]["content"][0], {"type": "tool_use", "id": "t1", "name": "read_file",
                                                 "input": {"path": "a.py"}})
        self.assertEqual([b["tool_use_id"] for b in msgs[2]["content"]], ["t1", "t2"])  # one user message
        self.assertEqual(msgs[3], {"role": "assistant", "content": ["RAW BLOCKS"]})

    def test_tool_call_round_trip_keeps_raw_blocks(self):
        thinking = FakeBlock(type="thinking", thinking="", signature="sig")
        use = FakeBlock(type="tool_use", id="tu_1", name="read_file", input={"path": "app/auth.py"})
        final = FakeBlock(type="text", text=json.dumps(ANALYSIS))
        llm, fake = self.make(claude_reply(thinking, use, stop="tool_use"), claude_reply(final))
        with TempDir() as tmp:
            text = run_tool_agent(llm, "sys", "goal", Repo(make_repo(tmp)))
        self.assertEqual(json.loads(text)["summary"], ANALYSIS["summary"])
        first, second = fake.calls
        self.assertTrue(first["beta"])
        self.assertEqual((first["fallbacks"], first["betas"]), ("default", ["server-side-fallback-2026-07-01"]))
        self.assertEqual(first["max_tokens"], 16000)
        self.assertNotIn("temperature", first)
        self.assertEqual(first["tools"][0]["name"], "list_files")
        self.assertIn("input_schema", first["tools"][0])
        self.assertIs(second["messages"][1]["content"][0], thinking)  # thinking sent back unchanged
        self.assertEqual(second["messages"][2]["content"][0]["tool_use_id"], "tu_1")
        self.assertIn("def login", second["messages"][2]["content"][0]["content"])

    def test_no_compaction_on_claude(self):
        llm, _ = self.make()
        self.assertGreater(llm.context_budget, 100000)

    def test_json_mode_and_models_without_fallback(self):
        llm, fake = self.make(claude_reply(FakeBlock(type="text", text='{"questions": []}')),
                              model="claude-haiku-5-5")
        self.assertEqual(stages.clarify_questions(llm, {"id": 1, "title": "t"}), [])
        self.assertFalse(fake.calls[0]["beta"])
        self.assertNotIn("fallbacks", fake.calls[0])
        self.assertIn("only a JSON object", fake.calls[0]["system"])
        self.assertEqual(llm.list_models(), ["claude-haiku-5-5", "claude-opus-5-5"])

    def test_refusal_and_errors(self):
        from taskmind.llm import LLMError, RequestTooLarge
        llm, fake = self.make(claude_reply(stop="refusal"))
        with self.assertRaises(LLMError) as ctx:
            llm.chat([{"role": "user", "content": "x"}])
        self.assertIn("declined", str(ctx.exception))
        llm, fake = self.make(FakeAnthropicModule.AuthenticationError("bad", 401))
        with self.assertRaises(LLMError) as ctx:
            llm.chat([{"role": "user", "content": "x"}])
        self.assertIn("rejected the API key", str(ctx.exception))
        llm, fake = self.make(FakeAnthropicModule.BadRequestError("prompt is too long", 400))
        with self.assertRaises(RequestTooLarge):
            llm.chat([{"role": "user", "content": "x"}])


def cfg_with(**over):
    base = {"provider": "groq", "keys": {"groq": "g", "gemini": "m", "anthropic": "a"},
            "models": {"gemini": "gem-1", "anthropic": "claude-opus-5-5"}, "base_url": None,
            "fallback": ["groq", "gemini", "anthropic"], "stages": {}, "review": "auto", "coder": None}
    base.update(over)
    return base


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"GROQ_API_KEY": "", "GEMINI_API_KEY": "", "ANTHROPIC_API_KEY": "",
                                                "OPENAI_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        from taskmind import routing
        self.routing = routing

    def names(self, cands):
        return [c["provider"] for c in cands]

    def test_candidates_order_dedupe_and_skip_unusable(self):
        cfg = cfg_with(fallback=["groq", "openai", "gemini", "anthropic"])  # openai: no key, no model
        self.assertEqual(self.names(self.routing.candidates(cfg, "clarify")), ["groq", "gemini", "anthropic"])

    def test_fallback_can_name_a_model(self):
        cfg = cfg_with(fallback=["groq", "groq:openai/gpt-oss-20b", "bogus"])
        cands = self.routing.candidates(cfg, "clarify")
        self.assertEqual([(c["provider"], c["model"]) for c in cands],
                         [("groq", "openai/gpt-oss-120b"), ("groq", "openai/gpt-oss-20b")])

    def test_stage_override_goes_first(self):
        cfg = cfg_with(stages={"analyze": "anthropic:claude-sonnet-5-5"})
        cands = self.routing.candidates(cfg, "analyze")
        self.assertEqual((cands[0]["provider"], cands[0]["model"]), ("anthropic", "claude-sonnet-5-5"))
        self.assertEqual(self.names(self.routing.candidates(cfg, "clarify"))[0], "groq")
        with self.assertRaises(ValueError):
            self.routing.parse_target("nope:model")
        self.assertEqual(self.routing.parse_target("openrouter:meta/llama:free"), ("openrouter", "meta/llama:free"))

    def test_review_avoids_coder_and_planner(self):
        cands, note = self.routing.review_candidates(cfg_with(coder="anthropic"), planned_by="groq")
        self.assertEqual(self.names(cands), ["gemini", "groq", "anthropic"])
        self.assertIsNone(note)

    def test_review_note_when_no_independent_ai(self):
        cfg = cfg_with(keys={"groq": "g"}, fallback=["groq"])
        cands, note = self.routing.review_candidates(cfg, planned_by="groq")
        self.assertEqual(self.names(cands), ["groq"])
        self.assertIn("Not an independent review", note)

    def test_review_explicit_and_off(self):
        cands, _ = self.routing.review_candidates(cfg_with(review="anthropic", coder="anthropic"))
        self.assertEqual(self.names(cands)[0], "anthropic")  # explicit choice wins
        cands, _ = self.routing.review_candidates(cfg_with(review="off", coder="groq"))
        self.assertEqual(self.names(cands)[0], "groq")

    def test_run_fails_over_and_reports(self):
        from taskmind.llm import LLMError
        cands = self.routing.candidates(cfg_with(), "clarify")
        attempts, switches = [], []

        def fake_from_settings(s):
            attempts.append(s["provider"])
            if s["provider"] == "groq":
                raise LLMError("Daily free-tier limit reached")
            return s["provider"]
        with mock.patch.object(self.routing, "from_settings", side_effect=fake_from_settings):
            result, used = self.routing.run(cands, lambda llm: f"done by {llm}",
                                            lambda cur, nxt, exc: switches.append((cur["provider"], nxt["provider"])))
        self.assertEqual(result, "done by gemini")
        self.assertEqual(used["provider"], "gemini")
        self.assertEqual(switches, [("groq", "gemini")])

    def test_run_all_fail_lists_every_error(self):
        from taskmind.llm import LLMError
        cands = self.routing.candidates(cfg_with(), "clarify")

        def boom(llm):
            raise LLMError(f"down {llm.model}")
        with mock.patch.object(self.routing, "from_settings", side_effect=lambda s: FakeBlock(model=s["model"])):
            with self.assertRaises(LLMError) as ctx:
                self.routing.run(cands, boom)
        self.assertIn("Every configured provider failed", str(ctx.exception))
        self.assertIn("gem-1", str(ctx.exception))


class TestFailoverCLI(TestCLI):
    def test_go_continues_on_backup_when_limit_hit(self):
        from taskmind.llm import LLMError
        root = Path(self.tmp.name) / "proj"
        root.mkdir()
        make_repo(root)
        self.run_cli("add", "Rate limit login", "--repo", str(root))
        backup = FakeLLM({"definition": "Limit logins.", "acceptance_criteria": ["429"]},
                         tool_call("search_code", pattern="login"), json.dumps(ANALYSIS), PLAN)

        class LimitedLLM:
            model = "gpt-oss"

            def chat(self, *a, **kw):
                raise LLMError("Daily free-tier limit reached for gpt-oss.")
        primary = {"provider": "groq", "label": "Groq", "model": "gpt-oss"}
        second = {"provider": "gemini", "label": "Gemini", "model": "gem-1"}
        with mock.patch.object(cli, "ensure_setup", return_value={}), \
                mock.patch.object(cli.routing, "candidates", return_value=[primary, second]), \
                mock.patch.object(cli.routing, "from_settings",
                                  side_effect=lambda s: LimitedLLM() if s is primary else backup):
            code, out = self.run_cli("go", "1", "--no-questions")
        self.assertEqual(code, 0, out)
        self.assertIn("Continuing with Gemini / gem-1", out)
        self.assertIn("TM-1.2", out)
        log = DB(Path(self.tmp.name) / "taskmind.db").get_task(1)["ai_log"]
        self.assertEqual({v["provider"] for v in log.values()}, {"gemini"})
        code, out = self.run_cli("show", "1")
        self.assertIn("analyze → gemini/gem-1", out)

    def test_config_options(self):
        code, out = self.run_cli("config", "--fallback", "groq,gemini", "--stage", "analyze=anthropic:claude-opus-5-5",
                                 "--coder", "anthropic", "--review", "auto")
        self.assertEqual(code, 0, out)
        from taskmind import config
        cfg = config.load()
        self.assertEqual(cfg["fallback"], ["groq", "gemini"])
        self.assertEqual(cfg["stages"], {"analyze": "anthropic:claude-opus-5-5"})
        self.assertEqual(cfg["coder"], "anthropic")
        self.run_cli("config", "--stage", "analyze=", "--coder", "none")
        cfg = config.load()
        self.assertEqual((cfg["stages"], cfg["coder"]), ({}, None))
        code, out = self.run_cli("config", "--stage", "deploy=groq")
        self.assertEqual(code, 1)
        code, out = self.run_cli("config", "--fallback", "groq,bogus")
        self.assertEqual(code, 1)


class TestVoice(unittest.TestCase):
    def setUp(self):
        from taskmind import voice
        self.voice = voice

    def test_chunks_respect_limit_and_sentences(self):
        text = "Short one. " + "word " * 80 + "end. Final sentence here!"
        pieces = self.voice.chunks(text, limit=60)
        self.assertTrue(all(len(p) <= 60 for p in pieces))
        self.assertEqual(" ".join(pieces).split(), text.split())
        self.assertEqual(self.voice.chunks("One. Two. Three.", limit=190), ["One. Two. Three."])

    def test_speakable(self):
        out = self.voice.speakable("🚧 **TM-12** next: TM-12.3 `write tests` → see https://x.io/a ✅")
        self.assertEqual(out, "task 12 next: task 12, step 3 write tests see")
        self.assertIn("नमस्ते", self.voice.speakable("नमस्ते TM-1"))  # other scripts survive

    def test_multipart(self):
        body, ctype = self.voice.multipart({"model": "m"}, "file", "a.wav", b"RIFFdata")
        boundary = ctype.split("boundary=")[1]
        self.assertIn(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nm\r\n".encode(), body)
        self.assertIn(b'filename="a.wav"', body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))

    def test_tool_detection(self):
        with mock.patch("shutil.which", side_effect=lambda n: "/x" if n == "arecord" else None):
            self.assertEqual(self.voice.recorder_command("f.wav", 5)[:2], ["arecord", "-q"])
        with mock.patch("shutil.which", side_effect=lambda n: "/x" if n == "spd-say" else None):
            self.assertEqual(self.voice.local_tts_command("-w x.wav")[0], ["spd-say", "--wait", "--", "-w x.wav"])
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(self.voice.recorder_command("f.wav"))
            self.assertIsNone(self.voice.local_tts_command("hi"))
            with self.assertRaises(self.voice.VoiceError):
                self.voice.record(lambda: None)

    def test_transcribe_prefers_groq_and_ignores_noise(self):
        import urllib.request
        cfg = {"provider": "groq", "keys": {"groq": "g", "openai": "o"}, "models": {}}
        sent = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        # A plain file in a temp folder: Windows can't reopen a NamedTemporaryFile that's still open.
        with TempDir() as tmp, mock.patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENAI_API_KEY": ""}):
            wav = Path(tmp) / "speech.wav"
            wav.write_bytes(b"RIFF" + b"\0" * 100)
            with mock.patch.object(urllib.request, "urlopen",
                                   side_effect=lambda req, timeout: sent.append(req) or Resp(b'{"text": " Fix login "}')):
                self.assertEqual(self.voice.transcribe(cfg, wav), "Fix login")
            with mock.patch.object(urllib.request, "urlopen", side_effect=lambda req, timeout: Resp(b'{"text": " ."}')):
                self.assertEqual(self.voice.transcribe(cfg, wav), "")
        self.assertTrue(sent[0].full_url.startswith("https://api.groq.com"))
        self.assertIn(b"whisper-large-v3-turbo", sent[0].data)
        self.assertEqual(sent[0].get_header("User-agent"), "taskmind/0.1")

    def test_speaker_falls_back_to_local_voice_once(self):
        cfg = {"provider": "groq", "keys": {"groq": "g"}, "models": {}}
        notes, runs = [], []
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}), \
                mock.patch.object(self.voice, "player_command", return_value=["aplay"]), \
                mock.patch.object(self.voice, "local_tts_command", side_effect=lambda t: (["spd-say", t], {})), \
                mock.patch.object(self.voice, "synthesize",
                                  side_effect=self.voice.VoiceError("needs a one-time terms acceptance")), \
                mock.patch("subprocess.run", side_effect=lambda cmd, **kw: runs.append(cmd)):
            speaker = self.voice.Speaker(cfg, notify=notes.append)
            self.assertEqual(speaker.engine, "groq")
            speaker.say("Task TM-4 is done.")
            speaker.say("Second.")
        self.assertEqual(len(notes), 1)
        self.assertIn("terms", notes[0])
        self.assertEqual(runs, [["spd-say", "Task task 4 is done."], ["spd-say", "Second."]])

    def test_speaker_off(self):
        speaker = self.voice.Speaker({"provider": "groq", "keys": {}, "models": {}, "voice": {"output": "off"}})
        self.assertIsNone(speaker.engine)
        speaker.say("nothing happens")

    def test_parse_spoken_task(self):
        llm = FakeLLM({"title": "Add rate limiting to login", "due": "2026-10-16", "priority": "HIGH",
                       "description": "per IP"})
        got = stages.parse_spoken_task(llm, "add rate limiting to login per IP high priority due friday",
                                       "2026-10-15")
        self.assertEqual(got, {"title": "Add rate limiting to login", "description": "per IP",
                               "due": "2026-10-16", "priority": "high", "remind_at": None})
        self.assertIn("Thursday", llm.calls[0]["messages"][-1]["content"])
        got = stages.parse_spoken_task(FakeLLM({"title": "Call mom", "remind_at": "2026-10-16T09:00:00"}),
                                       "remind me to call mom tomorrow morning", "2026-10-15 20:30")
        self.assertEqual(got["remind_at"], "2026-10-16 09:00")
        got = stages.parse_spoken_task(FakeLLM({"title": "", "due": "friday", "priority": None}), "buy milk",
                                       "2026-10-15")
        self.assertEqual((got["title"], got["due"], got["priority"]), ("buy milk", None, "medium"))


class TestVoiceCLI(TestCLI):
    def test_add_by_voice(self):
        llm = FakeLLM({"title": "Add CSV export", "description": "for notes", "due": "2026-10-20",
                       "priority": "high"})
        with mock.patch.object(cli, "listen", return_value="add csv export for notes, high, due the 20th"):
            code, out = self.run_cli("add", "--voice", "--no-repo", llm=llm, stdin="y\n")
        self.assertEqual(code, 0, out)
        task = DB(Path(self.tmp.name) / "taskmind.db").get_task(1)
        self.assertEqual((task["title"], task["priority"], task["due"]), ("Add CSV export", "high", "2026-10-20"))

    def test_add_by_voice_cli_options_win_and_cancel(self):
        llm = FakeLLM({"title": "Spoken", "priority": "high"})
        with mock.patch.object(cli, "listen", return_value="spoken"):
            code, out = self.run_cli("add", "--voice", "--no-repo", "-p", "low", llm=llm, stdin="n\n")
        self.assertIn("Not saved", out)
        self.assertIsNone(DB(Path(self.tmp.name) / "taskmind.db").get_task(1))
        code, out = self.run_cli("add", "--no-repo")
        self.assertEqual(code, 1)
        self.assertIn("--voice", out)

    def test_clarify_by_voice(self):
        self.run_cli("add", "Rate limit login", "--no-repo")
        llm = FakeLLM({"questions": ["Per IP or per user?", "What limit?"]},
                      {"definition": "Limit per IP.", "acceptance_criteria": ["429 after 5"]})
        spoken = []
        fake_speaker = FakeBlock(say=spoken.append)
        with mock.patch.object(cli, "make_speaker", return_value=fake_speaker), \
                mock.patch.object(cli, "listen", return_value="per IP"):
            # first answer spoken (Enter), second typed
            code, out = self.run_cli("clarify", "1", "--voice", llm=llm, stdin="\n5 per minute\n")
        self.assertEqual(code, 0, out)
        task = DB(Path(self.tmp.name) / "taskmind.db").get_task(1)
        self.assertEqual([c["a"] for c in task["clarifications"]], ["per IP", "5 per minute"])
        self.assertEqual(spoken[1:3], ["Per IP or per user?", "What limit?"])
        self.assertIn("1 acceptance criteria", spoken[-1])

    def test_today_speak(self):
        self.run_cli("add", "Fix TM-ref parsing", "--no-repo", "--due", "today")
        spoken = []
        with mock.patch.object(cli, "make_speaker", return_value=FakeBlock(say=spoken.append)):
            code, out = self.run_cli("today", "--speak")
        self.assertEqual(code, 0, out)
        self.assertIn("Due today: 1 task. TM-1 Fix TM-ref parsing", spoken[0])
        self.assertNotIn("📅", spoken[0])


class TestReminders(unittest.TestCase):
    def setUp(self):
        from taskmind import reminders
        self.r = reminders
        self.now = datetime(2026, 10, 15, 20, 30)  # a Thursday evening

    def test_parse_when(self):
        p = lambda t: self.r.parse_when(t, self.now)
        self.assertEqual(p("+30m"), "2026-10-15 21:00")
        self.assertEqual(p("+2h"), "2026-10-15 22:30")
        self.assertEqual(p("+1d"), "2026-10-16 20:30")
        self.assertEqual(p("21:15"), "2026-10-15 21:15")
        self.assertEqual(p("9am"), "2026-10-16 09:00")          # already past today -> tomorrow
        self.assertEqual(p("tomorrow 9:30 pm"), "2026-10-16 21:30")
        self.assertEqual(p("tomorrow"), "2026-10-16 09:00")
        self.assertEqual(p("tonight"), "2026-10-15 20:00")
        self.assertEqual(p("friday 10:30"), "2026-10-16 10:30")
        self.assertEqual(p("Thursday"), "2026-10-22 09:00")     # same weekday -> next week
        self.assertEqual(p("2026-11-01 14:00"), "2026-11-01 14:00")
        self.assertEqual(p("2026-11-01"), "2026-11-01 09:00")
        self.assertEqual(p("12am"), "2026-10-16 00:00")
        for bad in ("someday", "tomorrow 25:00", "+3w"):
            with self.assertRaises(ValueError):
                p(bad)

    def make_db(self):
        db = DB(":memory:")
        a = db.add_task("Call the bank")
        db.update_task(a, remind_at="2026-10-15 20:00")
        b = db.add_task("Pay rent", due="2026-10-15")
        c = db.add_task("Renew passport", due="2026-10-12")
        db.set_subtasks(c, [{"title": "Book appointment"}])
        d = db.add_task("Future", due="2026-10-20")
        db.update_task(d, remind_at="2026-10-16 09:00")
        e = db.add_task("Done one", due="2026-10-10")
        db.update_task(e, status="done")
        return db

    def test_pending_and_sent_once(self):
        db = self.make_db()
        cfg = {"reminders": {"time": "09:00"}}
        kinds = sorted((t["title"], k) for t, k, _, _ in self.r.pending(db, cfg, self.now))
        self.assertEqual(kinds, [("Call the bank", "remind"), ("Pay rent", "due"), ("Renew passport", "overdue")])
        shown = []
        with mock.patch.object(self.r, "notify", side_effect=lambda t, b: shown.append((t, b)) or True):
            spoken = []
            sent = self.r.check(db, cfg, spoken.append, self.now)
            self.assertEqual(len(sent), 3)
            self.assertIn(("Overdue by 3 days: Renew passport", "Next: Book appointment"), shown)
            self.assertEqual(len(spoken), 3)
            self.assertEqual(self.r.check(db, cfg, None, self.now), [])  # never twice
            next_day = datetime(2026, 10, 16, 9, 5)
            again = self.r.check(db, cfg, None, next_day)
        self.assertEqual(sorted(again), sorted(["Overdue by 4 days: Renew passport (Next: Book appointment)",
                                                "Overdue by 1 day: Pay rent", "Reminder: Future"]))

    def test_daily_time_gates_due_reminders(self):
        db = self.make_db()
        early = datetime(2026, 10, 15, 7, 0)
        kinds = {k for _, k, _, _ in self.r.pending(db, {"reminders": {"time": "08:30"}}, early)}
        self.assertEqual(kinds, set())

    def test_cron_line_keeps_session_and_no_keys(self):
        env = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
               "GROQ_API_KEY": "gsk_secret", "TASKMIND_HOME": "/tmp/tm 100%"}
        with mock.patch.dict(os.environ, env):
            line = self.r.cron_line("/opt/taskmind/tm", speak=True)
        self.assertTrue(line.startswith("*/5 * * * * "))
        self.assertIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", line)
        self.assertIn("remind check --quiet --speak", line)
        self.assertNotIn("gsk_secret", line)
        self.assertIn("100\\%", line)  # % is special in crontab
        self.assertTrue(line.endswith(self.r.CRON_TAG))

    def test_enable_disable_keeps_other_cron_lines(self):
        state = {"tab": "0 1 * * * backup.sh\n"}

        def fake_run(cmd, **kw):
            if cmd == ["crontab", "-l"]:
                return FakeBlock(returncode=0, stdout=state["tab"])
            state["tab"] = kw["input"]
            return FakeBlock(returncode=0)
        with mock.patch("shutil.which", return_value="/usr/bin/crontab"), \
                mock.patch("subprocess.run", side_effect=fake_run):
            self.r.enable("/opt/tm")
            self.r.enable("/opt/tm")  # idempotent
            self.assertEqual(state["tab"].count(self.r.CRON_TAG), 1)
            self.assertTrue(self.r.schedule_enabled())
            self.r.disable()
        self.assertEqual(state["tab"], "0 1 * * * backup.sh\n")


class TestCalendar(unittest.TestCase):
    def test_ics(self):
        from taskmind import calendar_export as cal
        task = {"id": 3, "title": "Pay rent, before 5th; landlord", "description": "Line one\nline two",
                "due": "2026-10-20", "remind_at": "2026-10-19 18:30", "status": "planned"}
        steps = [{"title": "Transfer money " + "x" * 80, "done": 0}, {"title": "Done step", "done": 1}]
        text = cal.to_ics([(task, steps)])
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertIn("DTSTART;VALUE=DATE:20261020", text)
        self.assertIn("DTEND;VALUE=DATE:20261021", text)
        self.assertIn("DTSTART:20261019T183000", text)
        self.assertIn("DTEND:20261019T184500", text)
        self.assertIn(r"SUMMARY:Due: Pay rent\, before 5th\; landlord", text)
        self.assertEqual(text.count("BEGIN:VALARM"), 2)
        self.assertTrue(all(len(l.encode()) <= 75 for l in text.split("\r\n")))
        self.assertIn("UID:taskmind-3-due@taskmind", text)
        unfolded = text.replace("\r\n ", "")
        self.assertIn("Line one\\nline two", unfolded)
        self.assertNotIn("Done step", unfolded)

    def test_google_link(self):
        from taskmind import calendar_export as cal
        links = cal.google_links({"id": 1, "title": "Dentist", "due": "2026-10-20", "remind_at": None}, [])
        self.assertEqual(links[0][0], "due date")
        self.assertIn("dates=20261020%2F20261021", links[0][1])
        self.assertIn("text=Due%3A+Dentist", links[0][1])


class TestSimpleModeCLI(TestCLI):
    def test_simple_mode_wording_and_no_repo(self):
        root = Path(self.tmp.name) / "proj"
        root.mkdir()
        make_repo(root)
        self.run_cli("config", "--mode", "simple")
        cwd = os.getcwd()
        os.chdir(root)
        try:
            code, out = self.run_cli("add", "Plan Goa trip", "--remind", "+1h")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, out)
        self.assertIn("Task 1", out)
        self.assertNotIn("TM-1", out)
        self.assertNotIn("repo:", out)  # no git linking in simple mode
        self.assertIn("🔔 Reminder set", out)
        task = DB(Path(self.tmp.name) / "taskmind.db").get_task(1)
        self.assertIsNone(task["repo"])
        self.assertTrue(task["remind_at"])
        code, out = self.run_cli("list")
        self.assertIn("new", out)
        self.assertNotIn("inbox", out)
        llm = FakeLLM({"questions": []}, {"definition": "Trip.", "acceptance_criteria": []},
                      {"summary": "Trip"}, {"priority": "medium", "subtasks": [{"title": "Book train"}],
                                            "tests": ["Tickets in email"]})
        code, out = self.run_cli("go", "1", llm=llm)
        self.assertIn("Task 1 step 1", out)
        self.assertIn("Done when", out)
        self.assertNotIn("Claude Code", out)
        self.assertIn("tm done 1.1", out)

    def test_remind_and_calendar_commands(self):
        self.run_cli("add", "Pay rent", "--no-repo", "--due", "today")
        code, out = self.run_cli("remind", "set", "1", "tomorrow", "9am")
        self.assertEqual(code, 0, out)
        self.assertIn("reminder set for", out)
        code, out = self.run_cli("remind")
        self.assertIn("Pay rent", out)
        # "Due today" reminders wait for the daily reminder time; make that midnight so the
        # test doesn't depend on what time of day (or which time zone) it runs in.
        self.run_cli("config", "--remind-time", "00:00")
        with mock.patch.object(cli.reminders, "notify", return_value=True):
            code, out = self.run_cli("remind", "check")
        self.assertIn("Due today: Pay rent", out)
        code, out = self.run_cli("remind", "set", "1", "someday")
        self.assertEqual(code, 1)
        ics = Path(self.tmp.name) / "out.ics"
        code, out = self.run_cli("calendar", "export", "-o", str(ics))
        self.assertEqual(code, 0, out)
        self.assertIn("Exported 2 event(s)", out)
        self.assertIn("SUMMARY:Due: Pay rent", ics.read_text())
        code, out = self.run_cli("calendar", "link", "1")
        self.assertIn("calendar.google.com", out)
        code, out = self.run_cli("config", "--remind-time", "8:15am")
        from taskmind import config
        self.assertEqual(config.load()["reminders"]["time"], "08:15")


class TestPackaging(unittest.TestCase):
    def test_bundled_guide_matches_readme(self):
        root = Path(__file__).resolve().parent.parent
        self.assertEqual((root / "taskmind" / "guide.md").read_text(encoding="utf-8"),
                         (root / "README.md").read_text(encoding="utf-8"),
                         "Run python3 scripts/build.py to update taskmind/guide.md")

    def test_output_never_crashes_on_unprintable_text(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with mock.patch("sys.stdout", stream):
            cli.safe_output()
            print("✅ done 🎉 नमस्ते")
            stream.flush()
        self.assertIn(b"done", raw.getvalue())


class TestReviewRegressions(unittest.TestCase):
    """One test per bug found in the independent review."""

    def test_non_object_json_replies_never_crash(self):
        from taskmind.llm import LLMError
        llm = FakeLLM('["What deadline?"]', '{"questions": ["What deadline?"]}')
        self.assertEqual(stages.clarify_questions(llm, {"id": 1, "title": "t"}), ["What deadline?"])
        with self.assertRaises(LLMError):  # two bad replies: a clean error that triggers failover
            stages.clarify_questions(FakeLLM("[]", "[1]"), {"id": 1, "title": "t"})
        with TempDir() as tmp:
            repo = Repo(make_repo(tmp))
            bad_args = {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "list_files", "arguments": '["src"]'}}]}
            text = run_tool_agent(FakeLLM(bad_args, json.dumps(ANALYSIS)), "s", "u", repo)
            self.assertIn("summary", text)
            result = stages.analyze(FakeLLM('[{"summary": "x"}]', json.dumps(ANALYSIS)),
                                    {"id": 1, "title": "Rate limit login"}, repo)
            self.assertEqual(result["summary"], ANALYSIS["summary"])

    def test_transport_failures_become_llm_errors(self):
        import http.client
        import urllib.request
        from taskmind.llm import LLM, LLMError

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        for failure in (TimeoutError("timed out"), ConnectionResetError(), http.client.RemoteDisconnected()):
            with mock.patch.object(urllib.request, "urlopen", side_effect=failure), mock.patch("time.sleep"):
                with self.assertRaises(LLMError):
                    LLM("http://x", "m").chat([{"role": "user", "content": "hi"}])
        for body in (b"<html>Bad gateway</html>", b'{"choices": null}'):
            with mock.patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: Resp(body)):
                with self.assertRaises(LLMError):
                    LLM("http://x", "m").chat([{"role": "user", "content": "hi"}])

    @unittest.skipIf(os.name == "nt", "symlinks need admin rights on Windows")
    def test_search_ignores_symlinks_out_of_repo_and_finds_unicode_names(self):
        with TempDir() as tmp:
            (Path(tmp) / "secret.txt").write_text("SECRET_KEY=abc\n")
            root = Path(tmp) / "r"
            root.mkdir()
            (root / "link.txt").symlink_to(Path(tmp) / "secret.txt")
            (root / "café.py").write_text("needle\n")
            git(root, "init", "-q")
            git(root, "add", ".")
            repo = Repo(root)
            self.assertIn("No matches", repo.search("SECRET"))
            self.assertIn("café.py", repo.files())
            self.assertIn("café.py:1", repo.search("needle"))

    def test_affected_files_as_string(self):
        self.assertEqual(stages._normalize_analysis({"affected_files": "none"})["affected_files"], [])
        self.assertEqual(stages._normalize_analysis({"affected_files": "app.py"})["affected_files"],
                         [{"path": "app.py", "reason": ""}])

    def test_empty_reply_retry_sends_no_empty_assistant_turn(self):
        llm = FakeLLM("", '{"questions": []}')
        stages.clarify_questions(llm, {"id": 1, "title": "t"})
        self.assertFalse(any(m["role"] == "assistant" for m in llm.calls[1]["messages"]))

    @unittest.skipIf(os.name == "nt", "POSIX file permissions")
    def test_config_file_is_never_world_readable(self):
        from taskmind import config
        with TempDir() as tmp, mock.patch.dict(os.environ, {"TASKMIND_HOME": tmp}):
            old = os.umask(0o022)
            try:
                with mock.patch.object(Path, "chmod"):  # even without the final chmod
                    config.save({"keys": {"groq": "k"}})
            finally:
                os.umask(old)
            self.assertEqual(oct(config.config_path().stat().st_mode & 0o777), "0o600")

    def test_verify_counts_references_like_sync(self):
        from taskmind import verify
        with TempDir() as tmp:
            root = make_repo(tmp)
            for i, msg in enumerate(["Fix TM-1.1", "Finish TM-1.2.", "Done: TM-1.", "TM-11 other", "XTM-1 no"]):
                (root / f"f{i}.txt").write_text(msg)
                git(root, "add", ".")
                git(root, "commit", "-qm", msg)
            subjects = [c.split(" ", 1)[1] for c in verify.collect_changes(Repo(root), 1)["commits"]]
            self.assertEqual(sorted(subjects), ["Done: TM-1.", "Finish TM-1.2.", "Fix TM-1.1"])
            with self.assertRaises(verify.NothingToVerify):  # older commits don't count
                verify.collect_changes(Repo(root), 1, since="2099-01-01T00:00:00+00:00")

    def test_missing_git_does_not_crash(self):
        with TempDir() as tmp, \
                mock.patch("subprocess.run", side_effect=FileNotFoundError("git")):
            repo = Repo(tmp)
            self.assertFalse(repo.is_git)

    def test_bad_dates_never_stop_reminders(self):
        from taskmind import reminders
        db = DB(":memory:")
        db.add_task("Broken", due="2026-02-30")
        db.add_task("Pay rent", due="2026-10-15")
        found = reminders.pending(db, {"reminders": {"time": "09:00:00"}}, datetime(2026, 10, 15, 10))
        self.assertEqual([t["title"] for t, *_ in found], ["Pay rent"])
        got = stages.parse_spoken_task(FakeLLM({"title": "x", "due": "2026-02-30", "remind_at": "2026-10-15 25:99"}),
                                       "x", "2026-10-15")
        self.assertEqual((got["due"], got["remind_at"]), (None, None))

    def test_voice_failure_never_repeats_a_reminder(self):
        from taskmind import reminders
        db = DB(":memory:")
        db.add_task("Pay rent", due="2026-10-15")

        def broken_voice(text):
            raise TimeoutError("timed out")
        with mock.patch.object(reminders, "notify", return_value=True):
            self.assertEqual(len(reminders.check(db, {}, broken_voice, datetime(2026, 10, 15, 10))), 1)
            self.assertEqual(reminders.check(db, {}, broken_voice, datetime(2026, 10, 15, 10, 5)), [])

    def test_time_parsing_fixes(self):
        from taskmind import reminders
        thu_morning = datetime(2026, 10, 15, 10, 0)
        self.assertEqual(reminders.parse_when("thursday 18:00", thu_morning), "2026-10-15 18:00")
        self.assertEqual(reminders.parse_when("thursday 9:00", thu_morning), "2026-10-22 09:00")
        self.assertEqual(reminders.parse_when("tonight 9", thu_morning), "2026-10-15 21:00")
        self.assertEqual(reminders.parse_when("tonight 6am", thu_morning), "2026-10-15 06:00")

    def test_recording_is_deleted_on_error(self):
        from taskmind import voice
        with TempDir() as tmp:
            target = Path(tmp) / "rec.wav"
            proc = FakeBlock(poll=lambda: None, send_signal=lambda s: target.write_bytes(b"RIFF"),
                             wait=lambda timeout=None: 0)
            with mock.patch.object(voice, "recorder_command", return_value=["rec"]), \
                    mock.patch("subprocess.Popen", return_value=proc), mock.patch("time.sleep"):
                with self.assertRaises(voice.VoiceError):  # too short: "Nothing was recorded"
                    voice.record(lambda: None, target)
                self.assertFalse(target.exists())

                def ctrl_c():
                    target.write_bytes(b"x" * 10000)
                    raise KeyboardInterrupt
                with self.assertRaises(KeyboardInterrupt):
                    voice.record(ctrl_c, target)
                self.assertFalse(target.exists())

    def test_speech_keeps_symbol_meaning(self):
        from taskmind import voice
        self.assertEqual(voice.speakable("Budget is $500 > ₹300, ticket #42 = blocker"),
                         "Budget is 500 dollars greater than 300 rupees, ticket number 42 equals blocker")

    def test_calendar_has_sequence_and_last_modified(self):
        from taskmind import calendar_export as cal
        task = {"id": 1, "title": "x", "due": "2026-10-20", "updated_at": "2026-10-15T05:30:00+00:00"}
        text = cal.to_ics([(task, [])])
        self.assertIn("LAST-MODIFIED:20261015T053000Z", text)
        self.assertIn("SEQUENCE:", text)

    def test_cron_line_has_path_and_disable_removes_empty_crontab(self):
        from taskmind import reminders
        with mock.patch.dict(os.environ, {"PATH": "/opt/homebrew/bin:/usr/bin"}):
            self.assertIn("PATH=/opt/homebrew/bin:/usr/bin", reminders.cron_line("/opt/tm"))
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return FakeBlock(returncode=0, stdout=f"*/5 * * * * x {reminders.CRON_TAG}\n")
        with mock.patch("shutil.which", return_value="/usr/bin/crontab"), mock.patch("subprocess.run", side_effect=fake_run):
            reminders.disable()
        self.assertIn(["crontab", "-r"], calls)


class TestReviewRegressionsCLI(TestCLI):
    def test_sync_continues_past_a_broken_task(self):
        good = Path(self.tmp.name) / "good"
        good.mkdir()
        make_repo(good)
        bad = Path(self.tmp.name) / "bad"
        bad.mkdir()
        make_repo(bad)
        self.run_cli("add", "Broken", "--repo", str(bad))
        self.run_cli("add", "Good", "--repo", str(good))
        remove_tree(bad)
        (good / "x.txt").write_text("x")
        git(good, "add", ".")
        git(good, "commit", "-qm", "Start TM-2")
        db = DB(Path(self.tmp.name) / "taskmind.db")
        db.conn.execute("UPDATE tasks SET created_at = '2000-01-01T00:00:00+00:00'")
        db.conn.commit()
        code, out = self.run_cli("sync")
        self.assertEqual(code, 0, out)
        self.assertIn("TM-1:", out)
        self.assertIn("TM-2 started", out)

    def test_cli_input_fixes(self):
        self.run_cli("add", "x", "--no-repo")
        code, out = self.run_cli("edit", "1", "--due", "friday")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("remind", "set", "TM-1", "tomorrow", "9am")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("prompt", "1", "-o", "/nonexistent/dir/x.md")
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out)
        code, out = self.run_cli("add", "fix [/] parser", "--no-repo", "--remind", "+0m")
        with mock.patch.object(cli.reminders, "notify", return_value=True):
            code, out = self.run_cli("remind", "check")
        self.assertEqual(code, 0, out)
        self.assertIn("fix [/] parser", out)

    def test_strict_fails_when_items_were_not_checked(self):
        root = Path(self.tmp.name) / "proj"
        root.mkdir()
        make_repo(root)
        self.run_cli("add", "Rate limit login", "--repo", str(root))
        db = DB(Path(self.tmp.name) / "taskmind.db")
        db.update_task(1, acceptance=["429 after 5 tries"], analysis={"edge_cases": []}, tests=[])
        (root / "app" / "auth.py").write_text("def login(): limit()\n")
        code, out = self.run_cli("verify", "1", "--strict", llm=FakeLLM({"summary": "?", "items": []}))
        self.assertEqual(code, 1)
        self.assertIn("could not be checked", out)


class TestTutorial(TestCLI):
    def test_plain_tm_shows_overview(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("WRITE IT DOWN", out)
        self.assertIn("tm tutorial", out)

    def test_full_tutorial_with_enter_everywhere(self):
        llm = FakeLLM({"questions": []},
                      {"definition": "Download notes as CSV.", "acceptance_criteria": ["Returns a CSV file"]},
                      tool_call("read_file", path="app.py"), json.dumps(ANALYSIS), PLAN,
                      {"summary": "Partly done", "items": [{"id": "A1", "status": "done", "evidence": "export_csv"}]})
        typed = iter(["tm add wrong", ""])  # a wrong command first, then Enter

        def ask(prompt):
            return next(typed) if prompt.strip() == ">" and "add" in ask.lesson else ""
        ask.lesson = "add"

        def fake_ask(prompt):
            value = ask(prompt)
            if prompt.strip() == ">" and value == "":
                ask.lesson = ""
            return value
        with mock.patch.object(cli, "interactive", return_value=True), \
                mock.patch.object(cli.ui, "ask", side_effect=fake_ask):
            code, out = self.run_cli("tutorial", llm=llm)
        self.assertEqual(code, 0, out)
        for lesson in range(1, 9):
            self.assertIn(f"Lesson {lesson} of 8", out)
        self.assertIn("Not quite", out)  # the wrong command was caught
        self.assertIn("TM-1.1 done", out)  # sync ticked the step from the practice commit
        self.assertIn("Verification", out)
        self.assertIn("Practice files removed", out)
        self.assertFalse((Path(self.tmp.name) / "tutorial").exists())
        self.assertIsNone(DB(Path(self.tmp.name) / "taskmind.db").get_task(1))  # real list untouched


class TestSteps(TestCLI):
    def db(self):
        return DB(Path(self.tmp.name) / "taskmind.db")

    def test_manage_steps_yourself(self):
        self.run_cli("add", "Plan offsite", "--no-repo")
        code, out = self.run_cli("step", "add", "1", "Book the venue", "-d", "Call 3 places")
        self.assertEqual(code, 0, out)
        self.assertIn("TM-1.1", out)
        self.run_cli("step", "add", "1", "Send invites")
        self.run_cli("step", "add", "TM-1", "Order food")
        self.assertEqual(self.db().get_task(1)["status"], "planned")  # a task with steps has a plan
        code, out = self.run_cli("step", "edit", "1.2", "Send calendar invites")
        self.assertIn("Updated step TM-1.2", out)
        self.run_cli("step", "rm", "1.3", "-y")
        self.run_cli("step", "add", "1", "Book transport")
        positions = [(s["position"], s["title"]) for s in self.db().subtasks(1)]
        self.assertEqual(positions, [(1, "Book the venue"), (2, "Send calendar invites"), (4, "Book transport")])
        code, out = self.run_cli("step", "list", "1")
        self.assertIn("Call 3 places", out)
        self.assertNotIn("Order food", out)
        for bad, expect in ((("step", "edit", "1"), "Say which step"), (("step", "rm", "1.3", "-y"), "no step 3"),
                            (("step", "add", "1"), "Give the step a title"), (("step", "edit", "1.1"), "new title")):
            code, out = self.run_cli(*bad)
            self.assertEqual(code, 1, bad)
            self.assertIn(expect, out)

    def test_remove_last_open_step_completes_task_and_add_reopens(self):
        self.run_cli("add", "x", "--no-repo")
        self.run_cli("step", "add", "1", "a")
        self.run_cli("step", "add", "1", "b")
        self.run_cli("done", "1.1")
        code, out = self.run_cli("step", "rm", "1.2", "-y")
        self.assertIn("complete", out)
        self.assertEqual(self.db().get_task(1)["status"], "done")
        self.run_cli("step", "add", "1", "c")
        self.assertEqual(self.db().get_task(1)["status"], "in_progress")

    def test_removed_steps_are_never_ticked_by_commits(self):
        db = DB(":memory:")
        tid = db.add_task("t")
        db.add_subtask(tid, "a")
        db.add_subtask(tid, "b")
        db.remove_subtask(tid, 2)
        self.assertFalse(db.mark_subtask(tid, 2, True))
        self.assertEqual(db.add_subtask(tid, "c"), 3)  # number 2 is not reused

    def test_replan_ignores_commits_for_the_old_plan(self):
        root = Path(self.tmp.name) / "proj"
        root.mkdir()
        make_repo(root)
        self.run_cli("add", "t", "--repo", str(root))
        db = self.db()
        db.conn.execute("UPDATE tasks SET created_at = '2000-01-01T00:00:00+00:00'")
        db.conn.commit()
        (root / "a.txt").write_text("a")
        git(root, "add", ".")
        git(root, "commit", "-qm", "old plan TM-1.1")
        db.set_subtasks(1, [{"title": "new first step"}])
        db.update_task(1, planned_at="2099-01-01T00:00:00+00:00")
        self.run_cli("sync")
        self.assertFalse(self.db().subtasks(1)[0]["done"])


class TestHelpAndAsk(TestCLI):
    def test_help_topics_and_lookup(self):
        code, out = self.run_cli("help")
        self.assertIn("Help topics", out)
        self.assertIn("Reminders", out)
        code, out = self.run_cli("help", "remind")
        self.assertIn("Desktop notifications", out)
        code, out = self.run_cli("help", "zzzz qqqq")
        self.assertIn("No help topic matches", out)

    def test_search_finds_the_right_section(self):
        from taskmind import helpdocs
        self.assertEqual(helpdocs.search("how do I set a reminder")[0][0], "Reminders")
        self.assertIn("Voice", [t for t, _ in helpdocs.search("can I talk to it with my microphone")])
        self.assertEqual(helpdocs.search(""), [])

    def test_ask_uses_ai_with_guide_state_and_commands(self):
        self.run_cli("config", "--key", "gsk_SECRET_123")
        self.run_cli("add", "Fix login", "--no-repo")
        llm = FakeLLM("Run `tm remind set 1 +2h`.")
        code, out = self.run_cli("ask", "how", "do", "I", "set", "a", "reminder?", llm=llm)
        self.assertEqual(code, 0, out)
        self.assertIn("tm remind set 1 +2h", out)
        prompt = llm.calls[0]["messages"][-1]["content"]
        self.assertIn("## Reminders", prompt)
        self.assertIn("TM-1 Fix login", prompt)
        self.assertIn("tm remind [-h]".replace(" [-h]", ""), prompt)
        self.assertNotIn("gsk_SECRET_123", prompt)  # keys never reach the model

    def test_ask_offline_when_no_ai(self):
        code, out = self.run_cli("ask", "how do I set a reminder")
        self.assertEqual(code, 0, out)
        self.assertIn("couldn't ask the AI", out)
        self.assertIn("Reminders", out)

    def test_ask_offline_when_key_rejected(self):
        from taskmind.llm import LLMError

        class Broken:
            model = "m"

            def chat(self, *a, **k):
                raise LLMError("The API key was rejected. Run `tm setup` to enter a new one.")
        code, out = self.run_cli("ask", "my key is not working", llm=Broken())
        self.assertIn("API key isn't working", out)
        self.assertIn("console.groq.com", out)
        self.assertIn("here's what the guide says", out)

    def test_ask_explains_key_problem_when_every_provider_fails(self):
        from taskmind.llm import LLMError
        with mock.patch.object(cli, "ensure_setup", return_value={}), \
                mock.patch.object(cli.routing, "candidates", return_value=[{"provider": "groq"}]), \
                mock.patch.object(cli, "AI") as ai:
            ai.return_value.run.side_effect = LLMError(
                "Every configured provider failed:\n  Groq / a: The API key was rejected.\n  Groq / b: The API key was rejected.")
            code, out = self.run_cli("ask", "help")
        self.assertIn("API key isn't working", out)
        self.assertIn("every provider failed; first: The API key was rejected", out)

    def test_questions_after_tm_go_to_the_brain(self):
        llm = FakeLLM({"type": "answer", "message": "Use `tm add \"...\"`."})
        code, out = self.run_cli("how", "do", "I", "add", "a", "task", llm=llm)
        self.assertEqual(code, 0, out)
        self.assertIn("tm add", out)
        self.assertEqual(len(llm.calls), 1)

    def test_friendly_errors(self):
        cases = {("lst",): "Did you mean `tm list`?", ("go",): "`tm go` needs a task number",
                 ("go", "abc"): "'abc' isn't a task number", ("remind", "sett"): "Did you mean `set`?",
                 ("done",): "needs a task or step number", ("show", "1", "2"): "`tm show` doesn't understand"}
        for argv, expect in cases.items():
            code, out = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(expect, out)
            self.assertNotIn("usage:", out)

    def test_command_reference_lists_every_command(self):
        ref = cli.command_reference()
        for command in ("add", "go", "step", "ask", "help", "remind", "calendar", "verify", "tutorial"):
            self.assertIn(f"tm {command}", ref)
        self.assertIn("+2h", ref)


class TestBrain(TestCLI):
    def db(self):
        return DB(Path(self.tmp.name) / "taskmind.db")

    def test_request_becomes_confirmed_commands(self):
        self.run_cli("add", "Let users download notes as CSV", "--no-repo")
        llm = FakeLLM({"type": "actions", "message": "Sure.", "actions": [
            {"argv": ["remind", "set", "1", "tomorrow", "9am"], "why": "reminder"},
            {"argv": ["tm", "edit", "1", "-p", "high"], "why": "priority"}]})
        code, out = self.run_cli("remind", "me", "about", "the", "CSV", "task", "tomorrow", "at", "9",
                                 llm=llm, stdin="y\n")
        self.assertEqual(code, 0, out)
        self.assertIn("I'll run:", out)
        self.assertIn("tm remind set 1 tomorrow 9am", out)
        task = self.db().get_task(1)
        self.assertEqual(task["priority"], "high")
        self.assertTrue(task["remind_at"].endswith("09:00"))
        prompt = llm.calls[0]["messages"][-1]["content"]
        self.assertIn("TM-1 Let users download notes as CSV", prompt)
        self.assertNotIn("tm setup", prompt.split("Command reference")[1].split("current state")[0])

    def test_declining_changes_nothing_and_dry_run(self):
        self.run_cli("add", "x", "--no-repo")
        plan = {"type": "actions", "actions": [{"argv": ["edit", "1", "-p", "low"]}]}
        code, out = self.run_cli("do", "make", "it", "low", llm=FakeLLM(plan), stdin="n\n")
        self.assertIn("Nothing was changed", out)
        code, out = self.run_cli("do", "--dry-run", "make it low", llm=FakeLLM(plan))
        self.assertIn("dry run", out)
        self.assertEqual(self.db().get_task(1)["priority"], "medium")

    def test_new_task_placeholder(self):
        llm = FakeLLM({"type": "actions", "actions": [
            {"argv": ["add", "Call the bank", "--no-repo"]}, {"argv": ["step", "add", "NEW", "Find the number"]},
            {"argv": ["remind", "set", "NEW", "+1h"]}]})
        code, out = self.run_cli("do", "add call the bank with a step and remind me in an hour", llm=llm, stdin="\n")
        self.assertEqual(code, 0, out)
        task = self.db().get_task(1)
        self.assertEqual(task["title"], "Call the bank")
        self.assertEqual(self.db().subtasks(1)[0]["title"], "Find the number")
        self.assertTrue(task["remind_at"])

    def test_deleting_needs_an_explicit_yes(self):
        self.run_cli("add", "old", "--no-repo")
        plan = {"type": "actions", "actions": [{"argv": ["rm", "1"]}]}
        code, out = self.run_cli("do", "delete the old task", llm=FakeLLM(plan), stdin="\n")  # Enter = no
        self.assertIn("⚠ deletes", out)
        self.assertIsNotNone(self.db().get_task(1))
        code, out = self.run_cli("do", "delete the old task", llm=FakeLLM(plan), stdin="y\n")
        self.assertIsNone(self.db().get_task(1))

    def test_invalid_or_forbidden_commands_are_corrected_or_refused(self):
        self.run_cli("add", "x", "--no-repo")
        bad = {"type": "actions", "actions": [{"argv": ["edit", "1", "--urgent", "high"]}]}
        good = {"type": "actions", "actions": [{"argv": ["edit", "1", "-p", "high"]}]}
        llm = FakeLLM(bad, good)
        code, out = self.run_cli("do", "make task 1 high", llm=llm, stdin="y\n")
        self.assertEqual(code, 0, out)
        self.assertIn("rejected", llm.calls[1]["messages"][-1]["content"])
        self.assertEqual(self.db().get_task(1)["priority"], "high")
        forbidden = {"type": "actions", "actions": [{"argv": ["config", "--key", "x"]}]}
        crontab = {"type": "actions", "actions": [{"argv": ["remind", "on"]}]}
        code, out = self.run_cli("do", "change my key", llm=FakeLLM(forbidden, forbidden, forbidden))
        self.assertEqual(code, 1)
        self.assertIn("isn't something I can run", " ".join(out.split()))
        code, out = self.run_cli("do", "turn on reminders", llm=FakeLLM(crontab, crontab, crontab))
        self.assertIn("run it yourself", " ".join(out.split()))

    def test_read_only_plans_run_without_asking(self):
        self.run_cli("add", "x", "--no-repo")
        llm = FakeLLM({"type": "actions", "actions": [{"argv": ["today"]}, {"argv": ["list"]}]})
        code, out = self.run_cli("do", "what should I work on today?", llm=llm, stdin="")
        self.assertEqual(code, 0, out)
        self.assertNotIn("Go ahead?", out)
        self.assertIn("Today", out)
        self.assertTrue(cli.is_read_only(["step", "list", "1"]))
        self.assertFalse(cli.is_read_only(["step", "add", "1", "x"]))
        self.assertFalse(cli.is_read_only(["prompt", "1"]))  # copies to the clipboard

    def test_unsupported_goes_to_wishlist(self):
        llm = FakeLLM({"type": "unsupported", "message": "TaskMind can't send emails yet."})
        code, out = self.run_cli("do", "email my boss the report", llm=llm)
        self.assertIn("can't send emails", out)
        code, out = self.run_cli("wishlist")
        self.assertIn("email my boss the report", out)

    def test_clarifying_question_round(self):
        self.run_cli("add", "Fix login", "--no-repo")
        self.run_cli("add", "Fix logout", "--no-repo")
        llm = FakeLLM({"type": "question", "message": "Which one: login (1) or logout (2)?"},
                      {"type": "actions", "actions": [{"argv": ["done", "2"]}]})
        with mock.patch.object(cli, "interactive", return_value=True):
            code, out = self.run_cli("do", "mark the fix task done", llm=llm, stdin="logout\ny\n")
        self.assertEqual(code, 0, out)
        self.assertIn("The user answered: logout", llm.calls[1]["messages"][-1]["content"])
        self.assertEqual(self.db().get_task(2)["status"], "done")

    def test_stops_at_first_failure(self):
        llm = FakeLLM({"type": "actions", "actions": [{"argv": ["done", "5"]}, {"argv": ["add", "never", "--no-repo"]}]})
        code, out = self.run_cli("do", "finish 5 then add", llm=llm, stdin="y\n")
        self.assertNotEqual(code, 0)
        self.assertIn("Stopped at step 1", out)
        self.assertIsNone(self.db().get_task(1))

    def test_no_ai_falls_back(self):
        code, out = self.run_cli("do", "how do I set a reminder")
        self.assertIn("couldn't ask the AI", out)
        self.assertIn("Reminders", out)

    def test_voice_request(self):
        llm = FakeLLM({"type": "actions", "actions": [{"argv": ["add", "Buy milk", "--no-repo"]}]})
        with mock.patch.object(cli, "listen", return_value="add buy milk"):
            code, out = self.run_cli("do", "--voice", llm=llm, stdin="\n")
        self.assertEqual(self.db().get_task(1)["title"], "Buy milk")

    def test_brain_normalizes_replies(self):
        got = stages.brain(FakeLLM({"type": "weird", "actions": [{"argv": "tm list"}, "bad", {"argv": []}]}),
                           "show list", "", "", "now")
        self.assertEqual(got["type"], "actions")
        self.assertEqual(got["actions"], [{"argv": ["list"], "why": ""}])


class TestSessionFeedback(TestCLI):
    """From the first full manual test session."""

    def test_confirm_asks_again_instead_of_guessing(self):
        with mock.patch.object(cli.ui, "ask", side_effect=['tm add "something"', "y"]):
            self.assertTrue(cli.ui.confirm("Ready?", default=True))
        with mock.patch.object(cli.ui, "ask", side_effect=["haan"]):
            self.assertTrue(cli.ui.confirm("Ready?"))
        with mock.patch.object(cli.ui, "ask", side_effect=["nahi"]):
            self.assertFalse(cli.ui.confirm("Ready?", default=True))
        with mock.patch.object(cli.ui, "ask", side_effect=["what", "huh", "eh"]):
            self.assertTrue(cli.ui.confirm("Ready?", default=True))  # gives up after 3: default

    def test_typo_is_fixed_without_ai(self):
        self.run_cli("add", "x", "--no-repo")
        with mock.patch.object(cli, "interactive", return_value=True):
            code, out = self.run_cli("og", "1", stdin="y\n")  # no llm given: must not need one
        self.assertIn("Did you mean `tm go 1`?", out)
        code, out = self.run_cli("shwo", "1")  # non-interactive: just the suggestion
        self.assertEqual(code, 2)
        self.assertIn("Did you mean `tm show 1`?", out)

    def test_step_ref_shows_step(self):
        self.run_cli("add", "x", "--no-repo")
        self.run_cli("step", "add", "1", "Write the script", "-d", "Use Python")
        code, out = self.run_cli("step", "1.1")
        self.assertEqual(code, 0, out)
        self.assertIn("Write the script", out)
        self.assertIn("Use Python", out)
        self.assertIn("not done yet", out)
        code, out = self.run_cli("step", "1")
        self.assertIn("Plan (0/1 done", out)
        code, out = self.run_cli("step", "1.7")
        self.assertIn("no step 7", out)

    def test_help_step_topic(self):
        code, out = self.run_cli("help", "step")
        self.assertIn("Your own steps", out)

    def test_spoken_command_cleanup(self):
        for said, expected in (("TM List", "List"), ("T.M. add buy milk.", "add buy milk"),
                               ("tm, show task 1", "show task 1"), ("team meeting at 5", "team meeting at 5")):
            self.assertEqual(cli.spoken_command(said), expected)

    def test_talk_loop_speaks_results(self):
        self.run_cli("add", "Call the bank", "--no-repo")
        spoken = []
        llm = FakeLLM({"type": "actions", "message": "Here is your list.", "actions": [{"argv": ["list"]}]})
        heard = iter(["what's on my list", "stop"])
        with mock.patch.object(cli, "interactive", return_value=True), \
                mock.patch.object(cli, "make_speaker", return_value=FakeBlock(say=spoken.append)), \
                mock.patch.object(cli, "listen", side_effect=lambda: next(heard)):
            code, out = self.run_cli("talk", llm=llm, stdin="\n\n")
        self.assertEqual(code, 0, out)
        self.assertIn("You have 1 open task: task 1, Call the bank.", spoken)
        self.assertEqual(spoken[-1], "Okay, bye!")

    def test_talk_accepts_typed_requests(self):
        spoken = []
        llm = FakeLLM({"type": "answer", "message": "Use tm add."})
        with mock.patch.object(cli, "interactive", return_value=True), \
                mock.patch.object(cli, "make_speaker", return_value=FakeBlock(say=spoken.append)), \
                mock.patch.object(cli, "listen", side_effect=AssertionError("should not listen")):
            code, out = self.run_cli("talk", llm=llm, stdin="how do I add a task\nstop\n")
        self.assertIn("Use tm add.", spoken)

    def test_voice_test_offers_to_run_what_you_said(self):
        llm = FakeLLM({"type": "actions", "actions": [{"argv": ["list"]}]})
        with mock.patch.object(cli, "make_speaker", return_value=FakeBlock(say=lambda t: None, engine="local",
                                                                         mode="auto", groq_key="k",
                                                                         describe=lambda: "offline")), \
                mock.patch.object(cli, "listen", return_value="list"):
            code, out = self.run_cli("voice", "--test", llm=llm, stdin="y\ny\n")
        self.assertIn("only a microphone test", out)
        self.assertIn("I'll run:", out)


class TestSecrets(unittest.TestCase):
    KEEP = ['token = request.headers.get("Authorization")', 'password = form.password.data',
            'secret_key = settings.SECRET_KEY', 'TOKEN_EXPIRY_SECONDS = 3600', 'API_KEY = os.environ["API_KEY"]',
            'const token = process.env.TOKEN;', 'password_field = "password"', 'self.api_key = api_key',
            '"auth": "Bearer ${TOKEN}"', 'DATABASE_URL = "postgres://localhost/dev"', 'secret = None',
            'token = user_token', '"token_type": "bearer"', 'def verify_token(token):']
    LEAK = ['API_KEY = "gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCD"', 'password = "hunter2hunter2"',
            'PASSWORD=hunter2hunter2', "db = 'postgres://admin:SuperSecret123@db/prod'",
            # Fake secrets are assembled from pieces so no file in the repo looks like a real key
            # (GitHub's push protection, rightly, refuses those).
            'stripe.api_key = "' + "sk_" + 'live_51HabcdefghijklmnopqrstuvWXYZ"', "AWS_ACCESS_KEY_ID=" + "AKIA" + "IOSFODNN7EXAMPLE",
            'client_secret: 9f8e7d6c5b4a39281706', "GITHUB_TOKEN=" + "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789",
            '"api_key": "a8f5f167f44f4964e6c998dee827110c"', "'password': 'P@ssw0rd!2024'",
            "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA " + "PRIVATE KEY-----"]

    def test_redaction(self):
        from taskmind.secrets import redact
        for line in self.KEEP:
            self.assertEqual(redact(line), line)
        for line in self.LEAK:
            self.assertIn("[REDACTED]", redact(line), line)
            self.assertNotIn("SuperSecret123", redact(line))

    def test_secret_files_never_reach_the_ai(self):
        from taskmind import verify
        with TempDir() as tmp:
            root = make_repo(tmp)
            (root / ".env").write_text("STRIPE_KEY=" + "sk_" + "live_51Habcdefghijklmnopqrstuvwxyz\n")
            (root / "id_rsa").write_text("-----BEGIN OPENSSH " + "PRIVATE KEY-----\nabc\n-----END OPENSSH " + "PRIVATE KEY-----\n")
            (root / "settings.py").write_text('API_KEY = "gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCD"\n')
            git(root, "add", "-f", ".")
            repo = Repo(root)
            self.assertNotIn(".env", repo.files())
            self.assertNotIn("id_rsa", repo.files())
            self.assertIn("never reads it", dispatch(repo, "read_file", {"path": ".env"}))
            self.assertNotIn("gsk_", dispatch(repo, "search_code", {"pattern": "KEY"}))
            self.assertNotIn("gsk_", dispatch(repo, "read_file", {"path": "settings.py"}))
            self.assertNotIn("gsk_", repo.overview())
            changes = verify.collect_changes(repo, 1)  # all of it is uncommitted work
            files = verify.split_files(changes["diff"])
            self.assertNotIn(".env", files)
            self.assertNotIn("sk_live", changes["diff"])
            self.assertNotIn("gsk_", changes["diff"])


class TestKnowingTheUser(TestCLI):
    def db(self):
        return DB(Path(self.tmp.name) / "taskmind.db")

    def test_profile(self):
        code, out = self.run_cli("me", "--name", "Kartik", "--language", "hinglish", "--hours", "09:00-18:00",
                                 "--coder", "Claude Code")
        self.assertEqual(code, 0, out)
        from taskmind import config
        self.assertEqual(config.load()["profile"]["name"], "Kartik")
        code, out = self.run_cli("me", "--hours", "9 to 6")
        self.assertEqual(code, 1)
        self.run_cli("add", "x", "--no-repo")
        state = cli.current_state(self.db())
        self.assertIn("name Kartik; preferred language hinglish", state)
        code, out = self.run_cli("today")
        self.assertIn("Kartik", out)

    def test_suggestions(self):
        from taskmind import insights
        db = self.db()
        old = "2026-01-01T00:00:00+00:00"
        a = db.add_task("Overdue thing", due="2026-01-05")
        b = db.add_task("Stuck thing")
        db.update_task(b, status="in_progress")
        c = db.add_task("Never planned")
        d = db.add_task("Finished code", repo="/code")
        db.set_subtasks(d, [{"title": "s"}])
        db.mark_subtask(d, 1, True)
        db.update_task(d, status="done")
        db.conn.execute("UPDATE events SET at = ?", (old,))
        db.conn.execute("UPDATE tasks SET created_at = ? WHERE id != ?", (old, d))
        db.conn.commit()
        e = db.add_task("Has reminder")
        db.update_task(e, remind_at="2099-01-01 09:00")
        tips = [t for t, _ in insights.suggestions(db, {}, reminders_on=False)]
        self.assertIn("overdue", tips[0])  # most urgent first
        joined = " | ".join(tips)
        for expect in ("no progress for", "not thought through", "never checked", "notifications are off"):
            self.assertIn(expect, joined)
        code, out = self.run_cli("today")
        self.assertIn("💡 Suggestions", out)

    def test_greeting(self):
        from taskmind import config, insights
        self.run_cli("me", "--name", "Kartik")
        self.run_cli("add", "Pay rent", "--no-repo", "--due", "today")
        text = insights.greeting(self.db(), config.load())
        self.assertIn("Kartik", text)
        self.assertIn("1 due today", text)
        code, out = self.run_cli("greet")
        self.assertIn("Good", out)
        with mock.patch.object(cli.reminders, "notify", return_value=True) as notify:
            self.run_cli("greet", "--notify")
        self.assertTrue(notify.called)

    def test_daily_greeting_once_per_day_and_only_in_a_terminal(self):
        self.run_cli("add", "x", "--no-repo")
        db = self.db()
        args = FakeBlock(command="list")
        buf = io.StringIO()
        buf.isatty = lambda: True  # pretend to be a terminal
        with contextlib.redirect_stdout(buf):
            cli.maybe_greet(args)
            cli.maybe_greet(args)
        self.assertEqual(buf.getvalue().count("Good "), 1)
        self.assertEqual(db.get_meta("last_greeting"), date.today().isoformat())
        code, out = self.run_cli("list")  # tests aren't a terminal: no greeting noise
        self.assertNotIn("Good ", out)

    def test_login_greeting_install(self):
        target = Path(self.tmp.name) / "autostart" / "taskmind-greet.desktop"
        with mock.patch.object(personal, "AUTOSTART", target), mock.patch.object(personal.sys, "platform", "linux"):
            code, out = self.run_cli("greet", "--install")
            self.assertEqual(code, 0, out)
            self.assertIn("greet --notify", target.read_text())
            self.run_cli("greet", "--uninstall")
            self.assertFalse(target.exists())

    def test_week(self):
        self.run_cli("add", "a", "--no-repo")
        self.run_cli("step", "add", "1", "s")
        self.run_cli("done", "1.1")
        self.run_cli("add", "late", "--no-repo", "--due", "2026-01-01")
        code, out = self.run_cli("week")
        self.assertEqual(code, 0, out)
        self.assertIn("1 task(s) finished", out)
        self.assertIn("2 added", out)
        self.assertIn("TM-2 late", out)


class TestDataSafety(TestCLI):
    def test_daily_backup_backup_restore_export(self):
        self.run_cli("add", "Keep me", "--no-repo")
        self.run_cli("list")
        backups = Path(self.tmp.name) / "backups"
        self.assertEqual(len(list(backups.glob("taskmind-*.db"))), 1)
        out_file = Path(self.tmp.name) / "manual.db"
        code, out = self.run_cli("backup", "-o", str(out_file))
        self.assertEqual(code, 0, out)
        self.run_cli("rm", "1", "-y")
        self.assertIsNone(DB(Path(self.tmp.name) / "taskmind.db").get_task(1))
        code, out = self.run_cli("backup", "--restore", str(out_file), stdin="y\n")
        self.assertEqual(code, 0, out)
        self.assertEqual(DB(Path(self.tmp.name) / "taskmind.db").get_task(1)["title"], "Keep me")
        self.assertTrue(list(backups.glob("before-restore-*.db")))
        exported = Path(self.tmp.name) / "all.json"
        self.run_cli("export", "-o", str(exported))
        data = json.loads(exported.read_text())
        self.assertEqual(data["tasks"][0]["title"], "Keep me")

    def test_old_backups_are_pruned(self):
        backups = Path(self.tmp.name) / "backups"
        backups.mkdir()
        for day in range(1, 11):
            (backups / f"taskmind-2026-01-{day:02d}.db").write_text("x")
        self.run_cli("add", "x", "--no-repo")
        self.run_cli("list")
        self.assertEqual(len(list(backups.glob("taskmind-*.db"))), cli.BACKUPS_KEPT)

    def test_two_writers_at_once_wait_instead_of_failing(self):
        import threading
        path = Path(self.tmp.name) / "concurrent.db"
        DB(path)
        errors = []

        def writer(n):
            try:
                db = DB(path)
                for i in range(30):
                    db.add_task(f"{n}-{i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(DB(path).list_tasks()), 120)


class TestPhase1(TestCLI):
    def test_code_consent_asked_once_per_project(self):
        root = Path(self.tmp.name) / "proj"
        root.mkdir()
        make_repo(root)
        self.run_cli("add", "Rate limit login", "--repo", str(root))
        llm = FakeLLM({"questions": []}, {"definition": "d", "acceptance_criteria": []})
        with mock.patch.object(cli, "interactive", return_value=True):
            code, out = self.run_cli("clarify", "1", "--no-questions", llm=FakeLLM(), stdin="n\n")
            self.assertEqual(code, 1)
            self.assertIn("nothing was sent", out)
            code, out = self.run_cli("clarify", "1", "--no-questions", llm=llm, stdin="y\n")
            self.assertEqual(code, 0, out)
            self.assertIn("Files with secrets", " ".join(out.split()))
            code, out = self.run_cli("clarify", "1", "--no-questions",
                                     llm=FakeLLM({"definition": "d2", "acceptance_criteria": []}))
            self.assertNotIn("Allow this for this project?", out)  # remembered
        from taskmind import config
        self.assertIn(str(root.resolve()), config.load()["projects_ok"])

    def test_unexpected_bug_is_friendly_and_logged_without_keys(self):
        self.run_cli("config", "--key", "gsk_SECRETKEY1234567890abcdefghij")
        with mock.patch.object(cli, "cmd_list", side_effect=ZeroDivisionError("boom")), \
                mock.patch.dict(os.environ, {"TASKMIND_DEBUG": ""}):
            code, out = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertIn("That's a bug, not something you did", " ".join(out.split()))
        self.assertNotIn("Traceback", out)
        code, out = self.run_cli("report-bug", "list", "crashed", "-o", str(Path(self.tmp.name) / "r.md"))
        report = (Path(self.tmp.name) / "r.md").read_text()
        self.assertIn("ZeroDivisionError: boom", report)
        self.assertIn("list crashed", report)
        logs = (Path(self.tmp.name) / "errors.log").read_text()
        self.assertNotIn("gsk_SECRET", report + logs)

    def test_key_never_logged_from_argv(self):
        from taskmind import diagnostics
        self.assertEqual(diagnostics.clean_argv(["config", "--key", "gsk_x123"]), ["config", "--key", "[REDACTED]"])
        self.assertEqual(diagnostics.clean_argv(["config", "--key=gsk_x123"]), ["config", "--key=[REDACTED]"])

    def test_feedback_and_issue_links(self):
        from taskmind import diagnostics
        code, out = self.run_cli("feedback", "plans", "have", "too", "many", "steps")
        self.assertIn("Thank you", out)
        self.assertIn("too many steps", (Path(self.tmp.name) / "feedback.txt").read_text())
        with mock.patch.object(diagnostics, "ISSUES_URL", None):
            self.assertEqual(diagnostics.issue_link("t", "b"), "")
        with mock.patch.object(diagnostics, "ISSUES_URL", "https://github.com/x/taskmind/issues/new"):
            link = diagnostics.issue_link("Bug: x", "body")
        self.assertTrue(link.startswith("https://github.com/x/taskmind/issues/new?title=Bug%3A+x"))


class TestLearningFromEdits(TestCLI):
    def test_edits_to_ai_plans_shape_the_next_plan(self):
        for n in (1, 2):
            self.run_cli("add", f"Task {n}", "--no-repo")
            plan = {"priority": "medium", "subtasks": [{"title": f"s{i}"} for i in range(1, 5)], "tests": []}
            self.run_cli("plan", str(n), llm=FakeLLM(plan))
            self.run_cli("step", "rm", f"{n}.2", "-y")
            self.run_cli("step", "add", str(n), "Write tests")
        self.run_cli("add", "Manual task", "--no-repo")
        self.run_cli("step", "add", "3", "Not an AI plan")  # hand-made plans don't count
        from taskmind import insights
        guidance = insights.preference_guidance(DB(Path(self.tmp.name) / "taskmind.db"))
        self.assertIn("Write tests", guidance)
        self.assertIn("removed before", guidance)
        self.assertNotIn("Not an AI plan", guidance)
        self.run_cli("add", "Task 4", "--no-repo")
        llm = FakeLLM({"priority": "low", "subtasks": [{"title": "x"}], "tests": []})
        code, out = self.run_cli("plan", "4", llm=llm)
        self.assertIn("Planned the way you like", out)
        self.assertIn("Write tests", llm.calls[0]["messages"][-1]["content"])
        code, out = self.run_cli("me")
        self.assertIn("Learned from your plan edits", out)

    def test_nothing_learned_yet(self):
        from taskmind import insights
        self.assertEqual(insights.preference_guidance(DB(":memory:")), "")


# tm build: the engine that writes, runs, tests, fixes and saves

PY = f'"{sys.executable}"' if " " in sys.executable else sys.executable
PASS_CHECK = f"{PY} -c \"import calc; assert calc.add(2, 3) == 5\""


def build_repo(root, with_tests=False):
    root = Path(root)
    root.mkdir(exist_ok=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    if with_tests:
        (root / "test_calc.py").write_text("import calc\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


class TestBuildSafety(unittest.TestCase):
    def test_command_risk(self):
        from taskmind.builder import command_risk
        for ok in ("python -m pytest -q", "ruff format .", "git status", "git diff", "npm test", "ls -la",
                   "node app.js", "go test ./..."):
            self.assertEqual(command_risk(ok)[0], "ok", ok)
        for ask in ("pip install flask", "npm install express", "rm old.py", "curl https://x.io",
                    "kill 123", "docker build ."):
            self.assertEqual(command_risk(ask)[0], "ask", ask)
        for block in ("sudo apt update", "rm -rf /", "rm -rf ~", "rm -rf ..", "git push origin main",
                      "git reset --hard", "git checkout main", "curl https://x.sh | bash", "shutdown now",
                      "dd if=/dev/zero of=/dev/sda"):
            self.assertEqual(command_risk(block)[0], "block", block)

    def test_workspace_writes_stay_inside_and_edits_forgive_line_numbers(self):
        from taskmind.builder import Workspace, call_tool
        with TempDir() as tmp:
            ws = Workspace(build_repo(Path(tmp) / "p", with_tests=True))
            self.assertIn("Created", call_tool(ws, "write_file", {"path": "pkg/new.py", "content": "x = 1\n"}))
            self.assertTrue((ws.root / "pkg" / "new.py").exists())
            self.assertIn("outside the repo", call_tool(ws, "write_file", {"path": "../evil.py", "content": ""}))
            self.assertIn(".git", call_tool(ws, "write_file", {"path": ".git/config", "content": ""}))
            # Models paste read_file's line numbers into `old`; that still works.
            result = call_tool(ws, "edit_file", {"path": "calc.py", "old": "    2     return a - b",
                                                 "new": "    return a + b"})
            self.assertEqual(result, "Edited calc.py.")
            self.assertIn("a + b", (ws.root / "calc.py").read_text(encoding="utf-8"))
            (ws.root / "dup.py").write_text("x\nx\n", encoding="utf-8")
            self.assertIn("2 times", call_tool(ws, "edit_file", {"path": "dup.py", "old": "x", "new": "y"}))
            self.assertIn("not found", call_tool(ws, "edit_file", {"path": "calc.py", "old": "nope", "new": "y"}))
            call_tool(ws, "write_file", {"path": "test_calc.py", "content": "import calc\n"})
            self.assertEqual(ws.changed, {"pkg/new.py", "calc.py", "test_calc.py"})
            self.assertEqual(ws.changed_tests(), ["test_calc.py"])  # it existed before: flag it

    def test_commands_ask_block_and_hide_keys(self):
        from taskmind.builder import Workspace
        asked = []
        with TempDir() as tmp, mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_" + "x" * 30, "MY_TOKEN": "t"}):
            ws = Workspace(build_repo(Path(tmp) / "p"), approve=lambda c, r: asked.append(c) or False)
            self.assertIn("Not allowed", ws.run("sudo ls"))
            self.assertIn("said no", ws.run("rm calc.py"))
            self.assertTrue((ws.root / "calc.py").exists())
            self.assertEqual(asked, ["rm calc.py"])
            out = ws.run(f"{PY} -c \"import os; print(os.environ.get('GROQ_API_KEY'), os.environ.get('MY_TOKEN'))\"")
            self.assertIn("exit code 0", out)
            self.assertIn("None None", out)

    def test_command_time_limit(self):
        from taskmind.builder import run_shell
        with TempDir() as tmp:
            code, out = run_shell(f"{PY} -c \"import time; time.sleep(30)\"", tmp, timeout=1)
        self.assertEqual(code, 124)
        self.assertIn("stopped after 1s", out)

    def test_detect_check(self):
        from taskmind.builder import detect_check
        with TempDir() as tmp:
            root = Path(tmp)
            self.assertEqual(detect_check(root), "")
            (root / "package.json").write_text('{"scripts": {"test": "echo \\"Error: no test specified\\""}}')
            self.assertEqual(detect_check(root), "")
            (root / "package.json").write_text('{"scripts": {"test": "jest"}}')
            self.assertEqual(detect_check(root), "npm test")
            (root / "package.json").unlink()
            (root / "test_x.py").write_text("")
            self.assertIn("-m", detect_check(root))

    def test_compact_shrinks_old_file_writes(self):
        from taskmind.builder import SHRUNK, _compact
        big = "x" * 5000
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        for i in range(10):
            messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "function": {
                "name": "write_file", "arguments": json.dumps({"path": f"f{i}.py", "content": big})}}]})
            messages.append({"role": "tool", "tool_call_id": str(i), "content": "Created"})
        _compact(messages, 40000)
        args = json.loads(messages[2]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(args["content"], SHRUNK)
        last = json.loads(messages[-2]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(last["content"], big)  # recent work stays whole


class TestBuildLoop(unittest.TestCase):
    def ws(self, tmp):
        from taskmind.builder import Workspace
        return Workspace(build_repo(Path(tmp) / "p"))

    def test_builds_tests_and_proves_it(self):
        from taskmind.builder import build
        llm = FakeLLM(
            tool_call("read_file", path="calc.py"),
            tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
            tool_call("run_command", command=PASS_CHECK),
            tool_call("finish", summary="add() now adds.", check_command=PASS_CHECK),
        )
        events = []
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp), on_event=lambda kind, **i: events.append(kind))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["changed"], ["calc.py"])
        self.assertEqual(report["summary"], "add() now adds.")
        self.assertIn("checked", events)

    def test_failed_check_goes_back_to_be_fixed(self):
        from taskmind.builder import build
        llm = FakeLLM(
            tool_call("finish", summary="Done (not really).", check_command=PASS_CHECK),
            tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
            tool_call("finish", summary="Fixed for real.", check_command=PASS_CHECK),
        )
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["rounds"], 1)
        feedback = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][-1]["content"]
        self.assertIn("it failed", feedback)
        self.assertIn("AssertionError", feedback)

    def test_keeps_failing_is_reported_honestly(self):
        from taskmind.builder import build
        finish = tool_call("finish", summary="Done.", check_command=PASS_CHECK)
        with TempDir() as tmp:
            report = build(FakeLLM(*[finish] * 3), "Fix add", self.ws(tmp), fix_rounds=2)
        self.assertEqual(report["status"], "failed")
        self.assertIn("AssertionError", report["check_output"])

    def test_the_users_check_wins_over_the_ais(self):
        from taskmind.builder import build
        llm = FakeLLM(tool_call("finish", summary="Done.", check_command=f"{PY} -c \"pass\""),
                      tool_call("finish", summary="Done.", check_command=f"{PY} -c \"pass\""))
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp), check=PASS_CHECK, fix_rounds=1)
        self.assertEqual(report["status"], "failed")  # the AI can't swap in an easier check
        self.assertEqual(report["check"], PASS_CHECK)

    def test_no_check_is_unverified_and_chatter_is_nudged(self):
        from taskmind.builder import build
        llm = FakeLLM("I will now edit the file.", tool_call("finish", summary="Done.", check_command=""))
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "unverified")
        self.assertIn("Keep going", llm.calls[1]["messages"][-1]["content"])

    def test_finish_written_as_text_still_gets_checked(self):
        # Seen live on Groq: after the tests passed, the model wrote finish's arguments as text.
        from taskmind.builder import build
        llm = FakeLLM(tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
                      json.dumps({"summary": "add() adds.", "check_command": PASS_CHECK}))
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"], "add() adds.")

    def test_stopping_twice_lets_the_tests_decide(self):
        from taskmind.builder import build
        llm = FakeLLM(tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
                      "All done.", "Really, all done.")
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp), check=PASS_CHECK)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"], "Really, all done.")

    def test_text_finish_that_fails_goes_back_to_be_fixed(self):
        from taskmind.builder import build
        llm = FakeLLM(json.dumps({"summary": "Done.", "check_command": PASS_CHECK}),
                      tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
                      tool_call("finish", summary="Fixed.", check_command=PASS_CHECK))
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "passed")
        self.assertIn("it failed", llm.calls[1]["messages"][-1]["content"])

    def test_tests_written_during_the_build_are_found_and_run(self):
        # Seen live: the AI wrote tests but described the command in text, so nothing was run.
        from taskmind.builder import build
        test = "import unittest, calc\n\nclass T(unittest.TestCase):\n    def test_add(self):\n" \
               "        self.assertEqual(calc.add(2, 3), 5)\n"
        llm = FakeLLM(tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
                      tool_call("write_file", path="test_calc.py", content=test),
                      "Done.", "Done. **Test command:** python -m unittest")
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "passed")
        self.assertIn("unittest", report["check"])

    def test_taskminds_own_test_detection_beats_a_wrong_ai_command(self):
        # Seen live: the AI's command found no tests (exit 5), and it spent its steps "fixing" working code.
        from taskmind.builder import build
        test = "import unittest, calc\n\nclass T(unittest.TestCase):\n    def test_add(self):\n" \
               "        self.assertEqual(calc.add(2, 3), 5)\n"
        llm = FakeLLM(tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
                      tool_call("write_file", path="tests/test_calc.py", content=test),
                      tool_call("finish", summary="Done.", check_command=f"{PY} -m unittest discover -s nowhere"))
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "passed", report["check_output"])

    def test_risky_check_is_not_run(self):
        from taskmind.builder import build
        llm = FakeLLM(tool_call("finish", summary="Done.", check_command="rm -rf build && pytest"))
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "unverified")

    def test_garbled_tool_call_retries_without_losing_progress(self):
        from taskmind.builder import build
        llm = FakeLLM(
            tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
            ToolCallError("attempted to call tool 'commentary'"),
            ToolCallError("attempted to call tool 'commentary'"),
            tool_call("finish", summary="Done.", check_command=PASS_CHECK),
        )
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp))
        self.assertEqual(report["status"], "passed")
        self.assertIn("invalid", llm.calls[-1]["messages"][-1]["content"])

    def test_out_of_steps(self):
        from taskmind.builder import build
        llm = FakeLLM(*[tool_call("list_files", path=f"d{i}") for i in range(5)])
        with TempDir() as tmp:
            report = build(llm, "Fix add", self.ws(tmp), max_steps=5)
        self.assertEqual(report["status"], "unfinished")

    def test_hinglish_for_non_programmers(self):
        from taskmind.builder import build
        llm = FakeLLM(tool_call("finish", summary="Ho gaya.", check_command=""))
        with TempDir() as tmp:
            build(llm, "Make a bill", self.ws(tmp), language="hinglish", simple=True)
        goal = llm.calls[0]["messages"][1]["content"]
        self.assertIn("not a programmer", goal)
        self.assertIn("Hinglish", goal)


class TestBuildCLI(TestCLI):
    def setUp(self):
        super().setUp()
        self.cwd = os.getcwd()
        self.root = build_repo(Path(self.tmp.name) / "buildproj")
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        super().tearDown()

    def branches(self):
        return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=self.root,
                              capture_output=True, text=True).stdout.split()

    @property
    def fixing_llm_goal(self):
        return self._llm.calls[0]["messages"][1]["content"]

    def fixing_llm(self):
        self._llm = FakeLLM(tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
                       tool_call("finish", summary="add() now adds.", check_command=PASS_CHECK))
        return self._llm

    def test_build_proves_and_keeps_the_work(self):
        base = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.root,
                              capture_output=True, text=True).stdout.strip()
        code, out = self.run_cli("build", "Fix", "the", "add", "function", "--yes", llm=self.fixing_llm())
        self.assertEqual(code, 0, out)
        self.assertIn("Done and checked", out)
        self.assertIn(f"Kept on {base}", out)
        self.assertEqual(self.branches(), [base])  # the work branch was merged and tidied away
        self.assertIn("a + b", (self.root / "calc.py").read_text(encoding="utf-8"))
        log = subprocess.run(["git", "log", "--format=%s"], cwd=self.root, capture_output=True, text=True).stdout
        self.assertIn("Fix the add function", log)
        code, out = self.run_cli("list", "--status", "done")
        self.assertIn("Fix the add function", out)

    def test_planned_task_ticks_its_steps(self):
        self.run_cli("add", "Fix add", "--repo", str(self.root))
        self.run_cli("step", "add", "1", "Change the operator")
        code, out = self.run_cli("build", "1", "--yes", llm=self.fixing_llm())
        self.assertEqual(code, 0, out)
        self.assertIn("Change the operator", self.fixing_llm_goal)
        code, out = self.run_cli("show", "1")
        self.assertIn("1/1", out)

    def test_unproven_work_stays_on_its_branch(self):
        llm = FakeLLM(tool_call("write_file", path="notes.md", content="hi\n"),
                      tool_call("finish", summary="Wrote notes.", check_command=""))
        code, out = self.run_cli("build", "Write notes", "--yes", llm=llm)
        self.assertEqual(code, 1)
        self.assertIn("not proven", out)
        self.assertIn("tm/write-notes", self.branches())
        self.assertIn("Left on branch", out)

    def test_refuses_to_mix_with_uncommitted_work(self):
        (self.root / "calc.py").write_text("# my unsaved change\n", encoding="utf-8")
        code, out = self.run_cli("build", "Fix add", "--yes", llm=self.fixing_llm())
        self.assertEqual(code, 1)
        self.assertIn("aren't committed", out)

    def test_secret_files_are_never_committed(self):
        llm = FakeLLM(tool_call("write_file", path=".env", content="KEY=1\n"),
                      tool_call("edit_file", path="calc.py", old="a - b", new="a + b"),
                      tool_call("finish", summary="Done.", check_command=PASS_CHECK))
        code, out = self.run_cli("build", "Fix add", "--yes", llm=llm)
        self.assertEqual(code, 0, out)
        tracked = subprocess.run(["git", "ls-files"], cwd=self.root, capture_output=True, text=True).stdout
        self.assertNotIn(".env", tracked)

    def test_new_project(self):
        llm = FakeLLM(tool_call("write_file", path="calc.py", content="def add(a, b):\n    return a + b\n"),
                      tool_call("finish", summary="Made it.", check_command=PASS_CHECK))
        code, out = self.run_cli("build", "A calculator", "--new", "calcapp", "--yes", llm=llm)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.root / "calcapp" / "calc.py").exists())


class TestBuildOutsideAProject(TestCLI):
    """Seen in real use: `tm build` in the home folder ran `git init` there."""

    def setUp(self):
        super().setUp()
        self.cwd = os.getcwd()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        (self.home / "photo.jpg").write_text("x", encoding="utf-8")  # a folder with the user's own things
        os.chdir(self.home)

    def tearDown(self):
        os.chdir(self.cwd)
        super().tearDown()

    def website_llm(self):
        return FakeLLM(tool_call("write_file", path="index.html", content="<h1>Cafe</h1>\n"),
                       tool_call("finish", summary="Made it.", check_command=""))

    def test_never_starts_git_in_a_folder_full_of_other_things(self):
        from taskmind.builder import NotAProject, start_branch
        with self.assertRaises(NotAProject):
            start_branch(self.home, "x")
        self.assertFalse((self.home / ".git").exists())

    def test_scripts_are_told_to_use_a_new_folder(self):
        code, out = self.run_cli("build", "Please create a coffee cafe website", llm=self.website_llm())
        self.assertEqual(code, 1)
        self.assertIn("--new coffee-cafe-website", out)
        self.assertFalse((self.home / ".git").exists())

    def test_builds_in_a_new_folder_instead(self):
        code, out = self.run_cli("build", "Please create one static website of a coffee cafe", "--yes",
                                 llm=self.website_llm())
        self.assertFalse((self.home / ".git").exists())
        self.assertTrue((self.home / "static-website-coffee" / "index.html").exists(), out)

    def test_an_empty_folder_becomes_the_project(self):
        empty = self.home / "empty"
        empty.mkdir()
        os.chdir(empty)
        self.run_cli("build", "A cafe website", "--yes", llm=self.website_llm())
        self.assertTrue((empty / ".git").exists())
        self.assertTrue((empty / "index.html").exists())

    def test_project_names(self):
        from taskmind.builder import project_name
        self.assertEqual(project_name("Please create one static website of coffice caffe"),
                         "static-website-coffice")
        self.assertEqual(project_name("Mujhe ek billing program chahiye"), "billing-program")
        self.assertEqual(project_name("please make a"), "my-project")
