"""Basics every command uses: errors, the database (with daily backups), finding tasks and projects."""

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import date

from taskmind import config
from taskmind.db import DB
from taskmind.repo_tools import Repo


class CLIError(Exception):
    pass


BACKUPS_KEPT = 7


def open_db():
    db = DB(config.db_path())
    auto_backup(db)
    return db


def backups_dir():
    path = config.home() / "backups"
    path.mkdir(exist_ok=True)
    return path


def auto_backup(db):
    """Once a day, before the first change, copy the task list; keep the last week."""
    today = date.today().isoformat()
    if db.get_meta("last_backup") == today or not db.list_tasks(include_done=True):
        return
    try:
        db.backup_to(backups_dir() / f"taskmind-{today}.db")
        for old in sorted(backups_dir().glob("taskmind-*.db"))[:-BACKUPS_KEPT]:
            old.unlink()
        db.set_meta("last_backup", today)
    except (OSError, sqlite3.Error):
        pass  # a failed backup must never stop the user's actual command


def get_task(db, task_id):
    task = db.get_task(task_id)
    if not task:
        raise CLIError(f"No task TM-{task_id}. See `tm list`.")
    return task


def resolve_repo(path):
    if not path:
        return None
    repo = Repo(path)  # validates the path
    return str(repo.root)


def detect_repo():
    """When run inside a git repo, link new tasks to it automatically."""
    try:
        repo = Repo(".")
    except ValueError:
        return None
    return str(repo.root) if repo.is_git else None


def task_repo(task):
    if not task.get("repo"):
        return None
    try:
        return Repo(task["repo"])
    except ValueError as exc:
        raise CLIError(f"{exc}. Fix it with `tm edit {task['id']} --repo <path>`.") from exc


def progress(db, task_id):
    subs = db.subtasks(task_id)
    return f"{sum(s['done'] for s in subs)}/{len(subs)}" if subs else "-"


def copy_to_clipboard(text) -> bool:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"],
                ["pbcopy"], ["clip.exe"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True, timeout=5)
                return True
            except (subprocess.SubprocessError, OSError):
                continue
    return False


def interactive():
    return sys.stdin.isatty()


def remove_tree(path, attempts=6) -> bool:
    """Delete a folder, including read-only files (git marks its objects read-only,
    and Windows refuses to delete those until they're made writable). On Windows,
    antivirus or indexing often holds just-created files for a moment, so retry
    briefly. Returns True when the folder is gone."""
    def make_writable_and_retry(func, target, _exc):
        try:
            for p in (os.path.dirname(target), target):  # the file, and the folder that holds it
                os.chmod(p, os.stat(p).st_mode | stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC * os.path.isdir(p))
            func(target)
        except OSError:
            pass
    import gc
    import time
    for attempt in range(attempts):
        if not os.path.exists(path):
            return True
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=make_writable_and_retry)
        else:
            shutil.rmtree(path, onerror=make_writable_and_retry)
        if not os.path.exists(path):
            return True
        gc.collect()  # drop anything still holding a file open
        time.sleep(0.25 * (attempt + 1))
    return not os.path.exists(path)
