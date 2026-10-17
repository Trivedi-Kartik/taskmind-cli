"""SQLite storage for tasks, subtasks and the activity log."""

import json
import sqlite3
import weakref
from datetime import datetime, timezone

# Every open database, so they can all be closed deterministically (Windows can't delete
# or replace a file that is still open, and WAL files are only tidied up on close).
_OPEN = weakref.WeakSet()


def close_all():
    for db in list(_OPEN):
        db.close()

STATUSES = ["inbox", "clarified", "analyzed", "planned", "in_progress", "done"]
PRIORITIES = ["high", "medium", "low"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '[]',
    clarifications TEXT NOT NULL DEFAULT '[]',
    analysis TEXT NOT NULL DEFAULT '{}',
    tests TEXT NOT NULL DEFAULT '[]',
    verification TEXT NOT NULL DEFAULT '{}',
    ai_log TEXT NOT NULL DEFAULT '{}',
    remind_at TEXT,
    planned_at TEXT,
    status TEXT NOT NULL DEFAULT 'inbox',
    priority TEXT NOT NULL DEFAULT 'medium',
    estimate_hours REAL,
    due TEXT,
    repo TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_source
    ON tasks(source, source_ref) WHERE source_ref IS NOT NULL;
CREATE TABLE IF NOT EXISTS subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',
    estimate_hours REAL,
    done INTEGER NOT NULL DEFAULT 0,
    done_ref TEXT,
    removed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (task_id, position)
);
CREATE TABLE IF NOT EXISTS reminders_sent (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (task_id, kind, key)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
"""

JSON_FIELDS = {"acceptance", "clarifications", "analysis", "tests", "verification", "ai_log", "files"}
TASK_FIELDS = {
    "title", "description", "definition", "acceptance", "clarifications", "analysis",
    "tests", "verification", "ai_log", "remind_at", "planned_at", "status", "priority", "estimate_hours", "due", "repo", "source", "source_ref",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decode(row) -> dict:
    data = dict(row)
    for key in JSON_FIELDS & data.keys():
        data[key] = json.loads(data[key]) if data[key] else None
    return data


class DB:
    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
        _OPEN.discard(self)

    def __init__(self, path):
        _OPEN.add(self)
        # timeout: wait (instead of failing) when another TaskMind, e.g. the reminder check, is writing.
        self.conn = sqlite3.connect(str(path), timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")  # readers and a writer don't block each other
        self.conn.executescript(SCHEMA)
        self._migrate()

    # Backups and export

    def backup_to(self, target):
        """A consistent copy of the database, even while it's in use."""
        dest = sqlite3.connect(str(target))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()

    def export(self) -> dict:
        tasks = []
        for task in self.list_tasks(include_done=True):
            tasks.append({**task, "steps": self.subtasks(task["id"]),
                          "activity": list(reversed(self.events(task["id"], limit=1000)))})
        return {"format": "taskmind-export-1", "exported_at": now(), "tasks": tasks}

    def _migrate(self):
        """Add columns introduced after a database was first created."""
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        added = {"verification": "TEXT NOT NULL DEFAULT '{}'", "ai_log": "TEXT NOT NULL DEFAULT '{}'",
                 "remind_at": "TEXT", "planned_at": "TEXT"}
        for name, decl in added.items():
            if name not in columns:
                with self.conn:
                    self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
        step_columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(subtasks)")}
        if "removed" not in step_columns:
            with self.conn:
                self.conn.execute("ALTER TABLE subtasks ADD COLUMN removed INTEGER NOT NULL DEFAULT 0")

    # Tasks

    def add_task(self, title, description="", repo=None, due=None, priority="medium",
                 source="manual", source_ref=None) -> int:
        ts = now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO tasks (title, description, repo, due, priority, source, source_ref,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (title, description, repo, due, priority, source, source_ref, ts, ts),
            )
        task_id = cur.lastrowid
        self.log(task_id, "created", f"Captured from {source}")
        return task_id

    def get_task(self, task_id):
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _decode(row) if row else None

    def find_by_source(self, source, source_ref):
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE source = ? AND source_ref = ?", (source, source_ref)
        ).fetchone()
        return _decode(row) if row else None

    def list_tasks(self, status=None, include_done=False):
        sql, args = "SELECT * FROM tasks", []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        elif not include_done:
            sql += " WHERE status != 'done'"
        sql += (" ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,"
                " due IS NULL, due, id")
        return [_decode(r) for r in self.conn.execute(sql, args)]

    def update_task(self, task_id, **fields):
        unknown = set(fields) - TASK_FIELDS
        if unknown:
            raise ValueError(f"Unknown task fields: {unknown}")
        if not fields:
            return
        values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self.conn:
            self.conn.execute(
                f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, now(), task_id),
            )

    def delete_task(self, task_id):
        with self.conn:
            self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    # Subtasks

    def set_subtasks(self, task_id, items):
        """Replace the whole plan (a new plan starts again at step 1; see tasks.planned_at)."""
        with self.conn:
            self.conn.execute("DELETE FROM subtasks WHERE task_id = ?", (task_id,))
            for pos, item in enumerate(items, start=1):
                self.conn.execute(
                    "INSERT INTO subtasks (task_id, position, title, details, files, estimate_hours)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (task_id, pos, item["title"], item.get("details", ""),
                     json.dumps(item.get("files", [])), item.get("estimate_hours")),
                )

    def subtasks(self, task_id):
        rows = self.conn.execute(
            "SELECT * FROM subtasks WHERE task_id = ? AND removed = 0 ORDER BY position", (task_id,)
        )
        return [_decode(r) for r in rows]

    def add_subtask(self, task_id, title, details="") -> int:
        """Append a step. Numbers are never reused, so commit references like TM-3.4 stay correct."""
        with self.conn:
            pos = self.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM subtasks WHERE task_id = ?",
                                    (task_id,)).fetchone()[0]
            self.conn.execute("INSERT INTO subtasks (task_id, position, title, details) VALUES (?, ?, ?, ?)",
                              (task_id, pos, title, details))
        return pos

    def update_subtask(self, task_id, position, **fields) -> bool:
        allowed = {k: v for k, v in fields.items() if k in ("title", "details") and v is not None}
        if not allowed:
            return False
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self.conn:
            cur = self.conn.execute(f"UPDATE subtasks SET {sets} WHERE task_id = ? AND position = ? AND removed = 0",
                                    (*allowed.values(), task_id, position))
        return cur.rowcount > 0

    def remove_subtask(self, task_id, position) -> bool:
        with self.conn:
            cur = self.conn.execute("UPDATE subtasks SET removed = 1 WHERE task_id = ? AND position = ? AND removed = 0",
                                    (task_id, position))
        return cur.rowcount > 0

    def mark_subtask(self, task_id, position, done=True, ref=None) -> bool:
        """Returns True when the subtask's state actually changed."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE subtasks SET done = ?, done_ref = ? WHERE task_id = ? AND position = ?"
                " AND done != ? AND removed = 0",
                (int(done), ref, task_id, position, int(done)),
            )
        return cur.rowcount > 0

    # Small key/value notes (last backup day, last greeting day ...)

    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        with self.conn:
            self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    # Reminders

    def reminder_sent(self, task_id, kind, key) -> bool:
        return self.conn.execute("SELECT 1 FROM reminders_sent WHERE task_id = ? AND kind = ? AND key = ?",
                                 (task_id, kind, key)).fetchone() is not None

    def mark_reminder_sent(self, task_id, kind, key):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO reminders_sent (task_id, kind, key, at) VALUES (?, ?, ?, ?)",
                              (task_id, kind, key, now()))

    # Activity log

    def log(self, task_id, kind, message):
        with self.conn:
            self.conn.execute(
                "INSERT INTO events (task_id, at, kind, message) VALUES (?, ?, ?, ?)",
                (task_id, now(), kind, message),
            )

    def events(self, task_id=None, since=None, limit=50):
        sql, args, where = "SELECT * FROM events", [], []
        if task_id is not None:
            where.append("task_id = ?")
            args.append(task_id)
        if since:
            where.append("at >= ?")
            args.append(since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]
