"""Read-only tools that let the agent explore a code repository.

Free-tier models have small token budgets, so every tool returns compact,
truncated output instead of whole files or whole repos.
"""

import fnmatch
import os
import re
import subprocess
import time
from pathlib import Path

from taskmind.secrets import is_secret_file, redact

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
    ".next", ".nuxt", "target", ".idea", ".vscode", "coverage", ".mypy_cache",
    ".pytest_cache", "vendor", ".gradle", "Pods",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".pdf", ".zip", ".gz",
    ".tar", ".jar", ".so", ".dll", ".exe", ".bin", ".lock", ".min.js", ".map", ".woff",
    ".woff2", ".ttf", ".mp4", ".mp3", ".pyc", ".class", ".db", ".sqlite",
}
MAX_FILES = 100_000        # listing names is cheap; the old 5,000 cut silently hid whole folders
MAX_FILE_BYTES = 400_000
MAX_TOOL_CHARS = 3000
SEARCH_SECONDS = 8         # a search over a huge repo stops here and says so


class Repo:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Repo path does not exist: {self.root}")
        self.is_git = self._git("rev-parse", "--is-inside-work-tree", check=False).strip() == "true"
        self._files = None
        self.truncated = 0   # files beyond MAX_FILES that aren't listed (0: all are)
        self._index = None
        self._index_stale = False

    def _git(self, *args, check=True) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",  # git speaks UTF-8; Windows' default code page doesn't
            )
        except FileNotFoundError as exc:
            if check:
                raise RuntimeError("git is not installed") from exc
            return ""
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"git {args[0]} took too long") from exc
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
        return result.stdout

    def _safe(self, rel) -> Path:
        path = (self.root / rel).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"Path is outside the repo: {rel}")
        return path

    def files(self) -> list:
        if self._files is None:
            if self.is_git:
                # -z: raw names (no quoting of non-ASCII paths like café.py)
                out = self._git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
                names = [n for n in out.split("\0") if n]
            else:
                names = []
                for dirpath, dirnames, filenames in os.walk(self.root):
                    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                    rel = Path(dirpath).relative_to(self.root)
                    names.extend((rel / f).as_posix() if str(rel) != "." else f for f in filenames)
                    if len(names) > MAX_FILES:
                        break
            listed = sorted(
                n for n in names
                if not any(n.endswith(s) for s in SKIP_SUFFIXES)
                and not any(part in SKIP_DIRS for part in Path(n).parts)
                and not is_secret_file(n)  # .env, keys, credentials: never shown to the AI
            )
            self.truncated = max(0, len(listed) - MAX_FILES)
            self._files = listed[:MAX_FILES]
        return self._files

    # The code index (taskmind/codeindex.py): built on first use, kept current by touch().

    def touch(self):
        """Files changed (the AI wrote or ran something): relist, and refresh the index on next use."""
        self._files = None
        self._index_stale = True

    def code_file_count(self) -> int:
        from taskmind.codeindex import language_of
        return sum(1 for f in self.files() if language_of(f))

    @property
    def index(self):
        from taskmind.codeindex import Index
        if self._index is None:
            self._index = Index.open(self)
            self._index_stale = False
        elif self._index_stale:
            self._index.refresh()
            self._index_stale = False
        return self._index

    def index_wanted(self, minimum) -> bool:
        """Is a code index worth having here? Not for tiny projects, and not if switched off."""
        return os.environ.get("TASKMIND_INDEX", "").lower() != "off" and self.code_file_count() >= minimum

    def tree(self, path=".", max_entries=200) -> str:
        prefix = "" if path in (".", "", "/") else path.strip("/") + "/"
        matches = [f for f in self.files() if f.startswith(prefix)]
        if not matches:
            return f"No files under '{path}'."
        note = (f"\n(Only the first {MAX_FILES:,} of {MAX_FILES + self.truncated:,} files are listed; "
                "name a folder to see inside it.)") if self.truncated else ""
        if len(matches) <= max_entries:
            return "\n".join(matches) + note
        # Too many files: summarize by directory instead.
        counts = {}
        for f in matches:
            rest = f[len(prefix):]
            top = rest.split("/", 1)[0] + ("/" if "/" in rest else "")
            counts[top] = counts.get(top, 0) + 1
        lines = [f"{prefix}{name}  ({n} files)" if name.endswith("/") else f"{prefix}{name}"
                 for name, n in sorted(counts.items())]
        return f"{len(matches)} files; showing directories:\n" + "\n".join(lines[:max_entries]) + note

    def search(self, pattern, glob=None, path=None, max_results=40) -> str:
        try:
            regex = re.compile(pattern, re.I)
        except re.error as exc:
            return f"Invalid regex: {exc}"
        prefix = "" if path in (None, "", ".", "/") else path.strip("/") + "/"
        hits = []
        deadline = time.monotonic() + SEARCH_SECONDS
        for name in self.files():
            if time.monotonic() > deadline:
                return ("\n".join(hits) + "\n" if hits else "") + (
                    f"(Search stopped after {SEARCH_SECONDS}s: the project is large. Narrow it with `path` "
                    "or `glob`, or use find_symbol for names.)")
            if prefix and not name.startswith(prefix):
                continue
            if glob and not fnmatch.fnmatch(name, glob) and not fnmatch.fnmatch(Path(name).name, glob):
                continue
            try:
                path = self._safe(name)  # skips symlinks that point outside the repo
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                with open(path, encoding="utf-8", errors="strict") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        if regex.search(line):
                            hits.append(f"{name}:{lineno}: {line.strip()[:160]}")
                            if len(hits) >= max_results:
                                return "\n".join(hits) + f"\n(stopped at {max_results} matches)"
            except (UnicodeDecodeError, OSError, ValueError):
                continue
        return "\n".join(hits) if hits else f"No matches for /{pattern}/."

    def read(self, path, start=1, end=None, max_lines=120) -> str:
        target = self._safe(path)
        if is_secret_file(target.relative_to(self.root)):
            return f"{path} looks like a secrets file (keys, passwords), so TaskMind never reads it."
        if not target.is_file():
            return f"Not a file: {path}"
        if target.stat().st_size > MAX_FILE_BYTES:
            return f"{path} is too large to read ({target.stat().st_size // 1000} KB)."
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return f"{path} is not a text file."
        start = max(1, int(start or 1))
        end = int(end) if end else 0
        if end < start:  # missing, or nonsense like start=260, end=200
            end = start + max_lines - 1
        end = min(len(lines), end, start + max_lines - 1)
        body = "\n".join(f"{n:>5} {lines[n - 1]}" for n in range(start, end + 1))
        more = f"\n... ({len(lines) - end} more lines)" if end < len(lines) else ""
        return f"{path} (lines {start}-{end} of {len(lines)})\n{body}{more}"

    def log(self, n=15, since=None) -> list:
        """Recent commits as dicts: sha, date, subject, body."""
        if not self.is_git:
            return []
        args = ["log", f"-n{int(n)}", "--format=%H%x1f%cI%x1f%s%x1f%b%x1e"]
        if since:
            args.append(f"--since={since}")
        out = self._git(*args, check=False)
        commits = []
        for record in out.split("\x1e"):
            parts = record.strip("\n").split("\x1f")
            if len(parts) == 4:
                commits.append(dict(zip(("sha", "date", "subject", "body"), parts)))
        return commits

    def overview(self) -> str:
        """A short, cheap summary used when tools are unavailable."""
        files = self.files()
        readme = next((f for f in files if Path(f).name.lower().startswith("readme")), None)
        text = f"Repository: {self.root.name} ({len(files)} files)\n\n{self.tree(max_entries=120)}"
        if readme:
            text += "\n\n" + self.read(readme, 1, 40)
        return redact(text)


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the repo, optionally under a sub-directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Sub-directory, default '.'"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Case-insensitive regex search across repo files. Returns file:line: text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression"},
                    "glob": {"type": "string", "description": "Optional file filter like '*.py'"},
                    "path": {"type": "string", "description": "Optional sub-directory to search in"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read part of a file with line numbers (max 120 lines per call). "
                           "Use search_code first to find the right line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_commits",
            "description": "Show recent git commit subjects to understand ongoing work.",
            "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}},
        },
    },
]


INDEX_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": "Where a function, class, method or constant is defined, from the code index: file, "
                           "line and which files import it. One step instead of searching text. Accepts "
                           "'name' or 'Class.method'; similar names are listed when there's no exact match.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_outline",
            "description": "A file's structure from the code index: every definition with its line number, "
                           "what the file imports and which files import it. Use it before reading a big file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
]


def tool_specs(repo) -> list:
    """The tools to offer for this repo: lookup tools join the list once the project is big enough
    for them to pay for the tokens their descriptions use."""
    from taskmind.codeindex import TOOLS_MIN_FILES
    return TOOL_SPECS + INDEX_TOOLS if repo.index_wanted(TOOLS_MIN_FILES) else TOOL_SPECS


def _arg(args, *names, default=None):
    """Models often guess argument names (line_start, start_line...); accept the common ones."""
    for name in names:
        if args.get(name) not in (None, ""):
            return args[name]
    return default


def dispatch(repo: Repo, name: str, args: dict, max_chars=MAX_TOOL_CHARS, max_lines=120) -> str:
    """Run one tool. Roomy providers get bigger reads (max_chars, max_lines): fewer calls,
    which matters where free tiers count requests per day."""
    try:
        if name == "list_files":
            result = repo.tree(_arg(args, "path", "dir", "directory", default="."))
        elif name == "search_code":
            result = repo.search(_arg(args, "pattern", "query", "regex"), args.get("glob"),
                                 _arg(args, "path", "dir", "directory"))
        elif name == "read_file":
            result = repo.read(_arg(args, "path", "file", "file_path"),
                               _arg(args, "start", "start_line", "line_start", "from_line", default=1),
                               _arg(args, "end", "end_line", "line_end", "to_line"), max_lines=max_lines)
        elif name == "find_symbol":
            result = repo.index.find_symbol(_arg(args, "name", "symbol", "query", default=""))
        elif name == "file_outline":
            result = repo.index.outline(_arg(args, "path", "file", "file_path", default=""))
        elif name == "recent_commits":
            commits = repo.log(min(int(args.get("n") or 15), 30))
            result = "\n".join(f"{c['sha'][:8]} {c['date'][:10]} {c['subject']}" for c in commits) or "No commits."
        else:
            result = f"Unknown tool: {name}"
    except (KeyError, ValueError, TypeError, AttributeError, RuntimeError, OSError) as exc:
        result = f"Tool error: {exc}"
    result = redact(result)  # every tool result, before it can reach the AI
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... (truncated)"
    return result
