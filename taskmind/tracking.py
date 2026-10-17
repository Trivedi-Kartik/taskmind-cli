"""Stage 7: keep task progress in sync with git commits and GitHub issue state."""

import re

from taskmind import github
from taskmind.repo_tools import Repo

REF = re.compile(r"\bTM-(\d+)(?:\.(\d+))?\b", re.I)


def refs_in(text):
    """Yield (task_id, subtask_position or None) for every TM-<id>[.<n>] reference."""
    for match in REF.finditer(text or ""):
        yield int(match.group(1)), int(match.group(2)) if match.group(2) else None


def finish_if_complete(db, task_id) -> bool:
    subtasks = db.subtasks(task_id)
    task = db.get_task(task_id)
    if subtasks and all(s["done"] for s in subtasks) and task["status"] != "done":
        db.update_task(task_id, status="done")
        db.log(task_id, "done", "All subtasks complete")
        return True
    return False


def sync_task(db, task, llm=None, match_commits=None) -> list:
    """Apply commit references (and optional AI matches) to a task. Returns change messages."""
    changes = []
    tid = task["id"]

    if task.get("repo"):
        repo = Repo(task["repo"])
        # Since the current plan: after `tm go --redo`, old "TM-3.1" commits belong to the old plan.
        commits = repo.log(n=200, since=task.get("planned_at") or task["created_at"])
        for commit in reversed(commits):  # oldest first
            for ref_id, pos in refs_in(commit["subject"] + "\n" + commit["body"]):
                if ref_id != tid:
                    continue
                if pos is not None and db.mark_subtask(tid, pos, True, commit["sha"][:10]):
                    changes.append(f"TM-{tid}.{pos} done ({commit['sha'][:8]} {commit['subject']})")
                    db.log(tid, "progress", changes[-1])
                if db.get_task(tid)["status"] not in ("in_progress", "done"):
                    db.update_task(tid, status="in_progress")
                    changes.append(f"TM-{tid} started ({commit['sha'][:8]})")
                    db.log(tid, "status", changes[-1])
        if llm and match_commits:
            unreferenced = [c for c in commits if not any(r == tid for r, _ in refs_in(c["subject"] + "\n" + c["body"]))]
            by_prefix = {c["sha"][:10]: c for c in unreferenced}
            for m in match_commits(llm, db.get_task(tid), db.subtasks(tid), unreferenced):
                commit = by_prefix.get(m["sha"][:10])
                if commit and db.mark_subtask(tid, m["subtask"], True, commit["sha"][:10]):
                    changes.append(f"TM-{tid}.{m['subtask']} done (AI match: {commit['sha'][:8]} {commit['subject']})")
                    db.log(tid, "progress", changes[-1])

    if task["source"] == "github" and task.get("source_ref"):
        owner_repo, _, number = task["source_ref"].rpartition("#")
        try:
            if github.issue_state(owner_repo, int(number)) == "closed" and db.get_task(tid)["status"] != "done":
                db.update_task(tid, status="done")
                changes.append(f"TM-{tid} done (GitHub issue {task['source_ref']} closed)")
                db.log(tid, "done", changes[-1])
        except github.GitHubError as exc:
            changes.append(f"TM-{tid}: could not check GitHub ({exc})")

    if finish_if_complete(db, tid):
        changes.append(f"TM-{tid} done (all subtasks complete)")
    return changes
