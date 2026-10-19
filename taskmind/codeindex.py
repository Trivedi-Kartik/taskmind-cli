"""A saved map of a project's code, so the AI can find things in one step instead of ten.

For every code file the index keeps the definitions in it (functions, classes, methods, constants,
page ids) with line numbers, and which other project files it imports. From that it answers:

  find_symbol(name)   where is this defined, and who uses that file
  file_outline(path)  a file's structure, what it imports, what imports it
  relevant(text)      which files a request is probably about, matched from names (no AI call)
  map_text()          the most connected files and their main definitions, in a few lines

The index is kept in TaskMind's own folder (never inside the project), keyed by the project's
path, and refreshed by looking at file sizes and times: only changed files are read again.
Secret files are never indexed (the repo listing excludes them), and only names and line numbers
are stored, never code. Import links are followed for Python, JavaScript/TypeScript and HTML; the
other languages get definitions only.
"""

import ast
import hashlib
import json
import math
import os
import posixpath
import re
import stat
import time
import warnings
from collections import defaultdict

from taskmind import config

VERSION = 2   # 2: definitions carry their end line
MAX_SYMBOLS = 300          # per file; generated files can define thousands
MAX_PARSE_BYTES = 400_000  # the same limit the AI's file reader has
MAP_MIN_FILES = 8          # fewer code files than this: the AI can just list them
TOOLS_MIN_FILES = 30       # from here on, lookup tools pay for the tokens their descriptions cost

LANG_BY_EXT = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".vue": "js",
    ".svelte": "js",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".java": "java", ".kt": "java", ".cs": "java", ".scala": "java",
    ".html": "html", ".htm": "html",
}
KIND_WORD = {"f": "function", "c": "class", "m": "method", "v": "constant", "t": "type", "i": "id"}
JS_EXTS = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".json",
           "/index.ts", "/index.tsx", "/index.js", "/index.jsx", "/index.mjs")

# --- reading a file's definitions -------------------------------------------------------------

_JS_FUNC = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)")
_JS_CLASS = re.compile(r"^(\s*)(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")
_JS_ARROW = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]{1,60})?=\s*"
                       r"(?:async\s+)?(?:\([^)]{0,200}\)|[A-Za-z_$][\w$]*)\s*(?::[^=]{1,60})?=>")
_JS_CONST = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Z][A-Z0-9_]{2,})\s*=")
_JS_TYPE = re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)")
_JS_METHOD = re.compile(r"^\s+(?:(?:static|async|public|private|protected|readonly|get|set)\s+)*"
                        r"([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\([^)]*\)\s*(?::[^{]{1,60})?\{")
_JS_NOT_METHODS = {"if", "for", "while", "switch", "catch", "function", "return", "else", "with", "do", "try"}
_JS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"]([^'"\n]{1,200})['"]""")

_REGEXES = {  # (compiled, kind, group of the name)
    "go": [(re.compile(r"^func\s+(?:\((?:\w+\s+)?\*?(\w+)(?:\[[^\]]*\])?\)\s*)?([A-Za-z_]\w*)"), "f", 2),
           (re.compile(r"^type\s+(\w+)\s+(?:struct|interface)"), "c", 1)],
    "rust": [(re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)"), "f", 1),
             (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+(\w+)"), "c", 1)],
    "ruby": [(re.compile(r"^\s*def\s+(?:self\.)?([\w?!=]+)"), "f", 1),
             (re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*)"), "c", 1)],
    "php": [(re.compile(r"^\s*(?:(?:public|protected|private|static|final|abstract)\s+)*function\s+&?(\w+)"), "f", 1),
            (re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+(\w+)"), "c", 1)],
    "java": [(re.compile(r"^\s*(?:(?:public|protected|private|static|final|abstract|sealed|data|open|internal"
                         r"|partial)\s+)*(?:class|interface|enum|record|object)\s+([A-Za-z_]\w*)"), "c", 1),
             (re.compile(r"^\s*(?:@\w+\s+)*(?:(?:public|protected|private|static|final|abstract|synchronized"
                         r"|override|suspend|async)\s+)+[\w<>\[\],.? ]+?\s+(\w+)\s*\([^;]*$"), "f", 1),
             (re.compile(r"^\s*(?:(?:public|private|protected|internal|override|suspend)\s+)*fun\s+(?:<[^>]*>\s*)?"
                         r"(?:\w+\.)?(\w+)"), "f", 1)],
}
_JAVA_NOT_METHODS = {"if", "for", "while", "switch", "catch", "return", "new", "else", "throw", "try"}
_HTML_ID = re.compile(r"""\bid\s*=\s*["']([A-Za-z][\w\-:.]{0,60})["']""")
_HTML_REF = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"'#?\s]{1,200})""")
_PY_FALLBACK = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+(\w+)")
_PY_IMPORT_FALLBACK = re.compile(r"^\s*(?:from\s+(\.*)([\w.]*)\s+import\s+([\w, *]+)|import\s+([\w., ]+))")


def language_of(path):
    return LANG_BY_EXT.get(os.path.splitext(path)[1].lower())


def _python(src):
    """([[name, kind, line]], [[level, module, [names]]]) for Python source."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # other people's code: invalid escapes etc. aren't our business
            tree = ast.parse(src)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _python_by_regex(src)
    symbols, imports = [], []

    def visit(body, prefix, in_class):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append([prefix + node.name, "m" if in_class else "f", node.lineno,
                                getattr(node, "end_lineno", None) or node.lineno])
            elif isinstance(node, ast.ClassDef):
                symbols.append([prefix + node.name, "c", node.lineno, getattr(node, "end_lineno", None) or node.lineno])
                visit(node.body, prefix + node.name + ".", True)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not in_class:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                symbols.extend([t.id, "v", node.lineno, getattr(node, "end_lineno", None) or node.lineno] for t in targets
                               if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 2)
            elif isinstance(node, (ast.If, ast.Try, ast.With)) and not in_class:
                visit(node.body, prefix, False)  # definitions guarded by `if` / `try` still count
                for handler in getattr(node, "handlers", []):
                    visit(handler.body, prefix, False)
                visit(getattr(node, "orelse", []), prefix, False)

    visit(tree.body, "", False)
    for node in ast.walk(tree):  # function-level imports count too
        if isinstance(node, ast.Import):
            imports.extend([0, alias.name, []] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append([node.level or 0, node.module or "", [a.name for a in node.names if a.name != "*"]])
    return symbols[:MAX_SYMBOLS], imports


def _python_by_regex(src):
    """When the file doesn't parse (Python 2, templates): definitions and imports by pattern."""
    symbols, imports, classes = [], [], []
    for number, line in enumerate(src.splitlines(), start=1):
        line = line[:300]
        m = _PY_FALLBACK.match(line)
        if m:
            indent, word, name = len(m.group(1)), m.group(2), m.group(3)
            while classes and classes[-1][0] >= indent:
                classes.pop()
            prefix = "".join(c[1] + "." for c in classes)
            symbols.append([prefix + name, "c" if word == "class" else ("m" if classes else "f"), number])
            if word == "class":
                classes.append((indent, name))
            continue
        m = _PY_IMPORT_FALLBACK.match(line)
        if m and m.group(4):
            imports.extend([0, part.strip().split(" as ")[0], []] for part in m.group(4).split(","))
        elif m:
            imports.append([len(m.group(1)), m.group(2), [n.strip() for n in m.group(3).split(",") if n.strip() != "*"]])
    return symbols[:MAX_SYMBOLS], imports


def _javascript(src):
    symbols, imports, class_name, class_indent = [], [], None, 0
    for number, line in enumerate(src.splitlines(), start=1):
        line = line[:300]  # minified or generated lines must not make the patterns slow
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*")):
            continue
        for spec in _JS_IMPORT.findall(line):
            imports.append([0, spec, []])
        if class_name and stripped.startswith("}") and len(line) - len(line.lstrip()) <= class_indent:
            class_name = None  # the class body ended
        m = _JS_CLASS.match(line)
        if m:
            symbols.append([m.group(2), "c", number])
            class_name, class_indent = m.group(2), len(m.group(1))
            continue
        for pattern, kind in ((_JS_FUNC, "f"), (_JS_ARROW, "f"), (_JS_TYPE, "t"), (_JS_CONST, "v")):
            m = pattern.match(line)
            if m:
                symbols.append([m.group(1), kind, number])
                break
        else:
            m = _JS_METHOD.match(line) if class_name else None
            if m and m.group(1) not in _JS_NOT_METHODS and len(line) - len(line.lstrip()) > class_indent:
                symbols.append([f"{class_name}.{m.group(1)}", "m", number])
    return symbols[:MAX_SYMBOLS], imports


def _by_patterns(src, lang):
    symbols = []
    patterns = _REGEXES[lang]
    for number, line in enumerate(src.splitlines(), start=1):
        line = line[:300]
        for pattern, kind, group in patterns:
            m = pattern.match(line)
            if not m:
                continue
            name = m.group(group)
            if lang == "java" and name in _JAVA_NOT_METHODS:
                break
            if lang == "go" and group == 2 and m.group(1):
                name = f"{m.group(1)}.{name}"  # a method: Receiver.Name
                kind = "m"
            symbols.append([name, kind, number])
            break
    return symbols[:MAX_SYMBOLS], []


def _html(src):
    symbols = [[ident, "i", src.count("\n", 0, m.start()) + 1] for m in _HTML_ID.finditer(src[:200_000])
               for ident in [m.group(1)]][:100]
    imports = [[0, ref, []] for ref in _HTML_REF.findall(src[:200_000])]
    return symbols, imports


def _with_ends(symbols, total_lines):
    """Give definitions without an end line one: the line before the next definition (at most 80 lines on)."""
    ordered = sorted((i for i, sym in enumerate(symbols) if len(sym) == 3), key=lambda i: symbols[i][2])
    starts = sorted({sym[2] for sym in symbols})
    for i in ordered:
        later = [n for n in starts if n > symbols[i][2]]
        end = (later[0] - 1) if later else total_lines
        symbols[i].append(max(symbols[i][2], min(end, symbols[i][2] + 80)))
    return symbols


def parse(lang, src):
    """(symbols, imports) for one file's text. A symbol is [name, kind, first line, last line]."""
    if lang == "python":
        symbols, imports = _python(src)
    elif lang == "js":
        symbols, imports = _javascript(src)
    elif lang == "html":
        symbols, imports = _html(src)
    else:
        symbols, imports = _by_patterns(src, lang)
    return _with_ends(symbols, src.count("\n") + 1), imports


# --- following imports to project files -------------------------------------------------------

def _py_roots(files):
    return [""] + [d for d in ("src", "lib", "app", "python") if any(f.startswith(d + "/") for f in files)]


def _py_find(parts, roots, fileset):
    for root in roots:
        base = posixpath.join(root, *parts) if root else posixpath.join(*parts)
        for candidate in (base + ".py", base + "/__init__.py"):
            if candidate in fileset:
                return candidate
    return None


def resolve_python(path, spec, roots, fileset):
    """The project files a Python import refers to: absolute (from the project root, src/, lib/ or the
    importing file's own folder) or relative. Anything that isn't a project file (os, requests) is left out.
    `from pkg import mod` links to mod.py, not to pkg/__init__.py: the package is only the way to it."""
    level, module, names = spec
    parts = [p for p in module.split(".") if p]
    here = posixpath.dirname(path)
    if level:
        base = here
        for _ in range(level - 1):
            base = posixpath.dirname(base)
        prefix, folders = [p for p in base.split("/") if p], [""]
    else:
        prefix, folders = [], roots + ([here] if here else [])
    module_hit = _py_find(prefix + parts, folders, fileset) if prefix + parts else None
    name_hits = []
    for name in names:
        hit = _py_find(prefix + parts + [name], folders, fileset)
        if hit and hit not in name_hits:
            name_hits.append(hit)
    found = name_hits if name_hits and (module_hit or "").endswith("/__init__.py") else \
        ([module_hit] if module_hit else []) + name_hits
    return [h for h in dict.fromkeys(found) if h != path]


def resolve_js(path, spec, fileset):
    """The project file a JavaScript/TypeScript import points at (relative paths and the common
    "@/" and "src/" styles); packages from node_modules are left out."""
    if spec.startswith("."):
        bases = [posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))]
    elif spec.startswith(("@/", "~/")):
        bases = ["src/" + spec[2:], spec[2:]]
    elif "/" in spec and not spec.startswith("@"):
        bases = [spec, "src/" + spec]
    else:
        return None
    for base in bases:
        stem, ext = os.path.splitext(base)
        for candidate_base in ([base, stem] if ext in (".js", ".jsx", ".mjs", ".cjs") else [base]):
            for suffix in JS_EXTS:
                hit = candidate_base + suffix
                if hit in fileset and hit != path:
                    return hit
    return None


def resolve_html(path, spec, fileset):
    if re.match(r"^([a-z][a-z0-9+.-]*:|//)", spec, re.I):
        return None
    hit = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
    return hit if hit in fileset and hit != path else None


# --- the index --------------------------------------------------------------------------------

_STOP = set("""a an and are as at be but by can do does for from get got had has have how i if in into is it its
me my no not of on or our so that the their them then there these they this to up us use was we were what when
where which who why will with would you your also all any each every just like more most must now only other
should some such than too very want need please make add create change fix update new one two page file code
thing things""".split())


_GENERIC_NAME_WORDS = {"cmd", "test", "tests", "get", "set", "is", "has", "do", "make"}


def tokens(text):
    """Lowercase words in text, with camelCase and snake_case split: 'computeInvoice_total' -> compute, invoice, total."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w for w in re.findall(r"[a-z][a-z0-9]*", spaced.lower())]


def cache_path(root):
    key = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:16]
    return config.home() / "index" / f"{key}.json"


class Index:
    def __init__(self, repo):
        self.repo = repo
        self.files = {}   # path -> [mtime_ns, size, lang, lines, symbols, imports]
        self.stats = {"parsed": 0, "removed": 0, "seconds": 0.0, "first": False}
        self._derived = False

    @classmethod
    def open(cls, repo):
        """Load the saved index (if any) and bring it up to date."""
        index = cls(repo)
        index._load()
        index.refresh()
        return index

    # saved copy

    def _load(self):
        try:
            data = json.loads(cache_path(self.repo.root).read_text(encoding="utf-8"))
            if data.get("version") == VERSION and data.get("root") == str(self.repo.root):
                self.files = {k: v for k, v in data["files"].items() if isinstance(v, list) and len(v) == 6}
        except (OSError, ValueError, KeyError, AttributeError):
            self.files = {}  # missing or damaged: start over, nothing is lost

    def save(self):
        path = cache_path(self.repo.root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": VERSION, "root": str(self.repo.root), "files": self.files},
                                      separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # an index that can't be saved still works for this run

    @staticmethod
    def saved_exists(repo) -> bool:
        return cache_path(repo.root).exists()

    @staticmethod
    def forget(repo):
        try:
            cache_path(repo.root).unlink()
        except OSError:
            pass

    # bringing it up to date

    def refresh(self):
        """Read again only the files whose size or time changed; forget deleted ones."""
        started, first = time.monotonic(), not self.files
        current, parsed = {}, 0
        root = str(self.repo.root)
        for name in self.repo.files():
            lang = language_of(name)
            if not lang:
                continue
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue  # symlinks could point outside the project
            old = self.files.get(name)
            if old and old[0] == st.st_mtime_ns and old[1] == st.st_size:
                current[name] = old
                continue
            current[name] = self._read(name, lang, st)
            parsed += 1
        removed = len(set(self.files) - set(current))
        self.files = current
        self.stats = {"parsed": parsed, "removed": removed, "first": first,
                      "seconds": round(time.monotonic() - started, 2)}
        if parsed or removed or first:
            self._derived = False
            self.save()
        return self.stats

    def _read(self, name, lang, st):
        symbols, imports, lines = [], [], 0
        if st.st_size <= MAX_PARSE_BYTES:
            try:
                with open(os.path.join(str(self.repo.root), name), encoding="utf-8", errors="replace") as fh:
                    src = fh.read()
                if "\0" not in src[:2000]:
                    lines = src.count("\n") + 1
                    symbols, imports = parse(lang, src)
            except (OSError, ValueError, RecursionError, MemoryError):
                symbols, imports = [], []
        return [st.st_mtime_ns, st.st_size, lang, lines, symbols, imports]

    # the links between files, and word lookups (built when first needed after a change)

    def _derive(self):
        if self._derived:
            return
        fileset = set(self.files)
        roots = _py_roots(fileset)
        self.deps, self.rdeps = {}, defaultdict(list)
        for name, (_, _, lang, _, _, imports) in self.files.items():
            targets = []
            for spec in imports:
                if lang == "python":
                    found = resolve_python(name, spec, roots, fileset)
                elif lang == "js":
                    hit = resolve_js(name, spec[1], fileset)
                    found = [hit] if hit else []
                elif lang == "html":
                    hit = resolve_html(name, spec[1], fileset)
                    found = [hit] if hit else []
                else:
                    found = []
                targets.extend(t for t in found if t not in targets)
            self.deps[name] = targets
            for target in targets:
                self.rdeps[target].append(name)
        self.by_name = defaultdict(list)   # lowercase name -> [(path, line, kind, qualified name)]
        self.word_files = defaultdict(set)  # word -> files whose path or definitions use it
        self.word_defs = defaultdict(list)  # word in a definition's name -> [(path, line, kind, qualified)]
        self.path_words = {}
        self.path_token_files = defaultdict(set)  # a word in a path -> files (for glued names: codeindex)
        self.stems = defaultdict(list)      # lowercase file name without extension -> paths
        for name, (_, _, _, _, symbols, _) in self.files.items():
            self.stems[posixpath.splitext(posixpath.basename(name))[0].lower()].append(name)
            self.path_words[name] = set(tokens(name))
            for word in self.path_words[name]:
                self.path_token_files[word].add(name)
            words = set()
            for qualified, kind, line, _end in symbols:
                bare = qualified.rsplit(".", 1)[-1]
                entry = (name, line, kind, qualified, _end)
                self.by_name[bare.lower()].append(entry)
                if "." in qualified:
                    self.by_name[qualified.lower()].append(entry)
                name_words = tokens(bare)
                words.update(name_words)
                for word in set(name_words):
                    self.word_defs[word].append(entry)
            for word in self.path_words[name] | words:
                self.word_files[word].add(name)
        self._derived = True

    # what the index knows

    def summary(self) -> dict:
        self._derive()
        langs = defaultdict(int)
        for entry in self.files.values():
            langs[entry[2]] += 1
        top = sorted(self.rdeps.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:8]
        return {"files": len(self.files), "symbols": sum(len(e[4]) for e in self.files.values()),
                "links": sum(len(v) for v in self.deps.values()), "languages": dict(sorted(langs.items(),
                key=lambda kv: -kv[1])), "most_imported": [(p, len(v)) for p, v in top], **self.stats}

    def _fmt_symbol(self, path, line, kind, qualified, _end=None):
        return f"{path}:{line} {KIND_WORD.get(kind, kind)} {qualified}"

    def find_symbol(self, name, limit=12) -> str:
        self._derive()
        name = (name or "").strip().strip("()")
        if not name:
            return "Give the name of a function, class, method or constant."
        key = name.lower()
        exact = self.by_name.get(key, [])
        partial = []
        if len(exact) < limit:
            seen = set(exact)
            for candidate, places in self.by_name.items():
                if key in candidate and candidate != key:
                    partial.extend(p for p in places if p not in seen and "." not in candidate)
            partial.sort(key=lambda p: (len(p[3]), p[0], p[1]))
        lines = []
        for path, line, kind, qualified, _end in exact[:limit]:
            lines.append(self._fmt_symbol(path, line, kind, qualified))
            users = self.rdeps.get(path, [])
            if users and len(lines) <= 3:
                lines.append(f"    imported by {len(users)} file{'s' if len(users) != 1 else ''}: "
                             + ", ".join(users[:5]) + (" ..." if len(users) > 5 else ""))
        if exact and len(exact) > limit:
            lines.append(f"... and {len(exact) - limit} more with that name")
        if partial:
            lines.append("Similar names:" if exact else f"No symbol named '{name}'. Names that contain it:")
            lines.extend("  " + self._fmt_symbol(*p) for p in partial[:6 if exact else 10])
        if not exact and not partial:
            return (f"No symbol named '{name}' in the index ({len(self.files)} code files). It may be spelled "
                    "differently, defined in a language the index doesn't read, or be text rather than a "
                    "definition: try search_code.")
        return "\n".join(lines)

    def _closest_paths(self, path):
        base = posixpath.basename(path).lower()
        return [p for p in self.files if posixpath.basename(p).lower() == base
                or base and base in p.lower()][:5]

    def outline(self, path) -> str:
        self._derive()
        path = (path or "").strip().lstrip("./")
        entry = self.files.get(path)
        if not entry:
            close = self._closest_paths(path)
            return (f"'{path}' isn't an indexed code file." +
                    (" Did you mean: " + ", ".join(close) + "?" if close else " Use list_files to see paths."))
        _, _, lang, lines, symbols, _ = entry
        out = [f"{path}  ({lines} lines, {lang})"]
        for qualified, kind, line, _end in sorted(symbols, key=lambda s: s[2]):
            depth = qualified.count(".") if kind == "m" or (kind == "c" and "." in qualified) else 0
            out.append(f"{line:>6} {'  ' * depth}{KIND_WORD.get(kind, kind)} {qualified.rsplit('.', 1)[-1]}")
        if len(out) == 1:
            out.append("  (no definitions found; read the file to see what's in it)")
        deps, users = self.deps.get(path, []), self.rdeps.get(path, [])
        if deps:
            out.append(f"imports {len(deps)}: " + ", ".join(deps[:10]) + (" ..." if len(deps) > 10 else ""))
        if users:
            out.append(f"imported by {len(users)}: " + ", ".join(users[:10]) + (" ..." if len(users) > 10 else ""))
        return "\n".join(out)

    def _rank(self, text):
        """(file scores, definition scores by file, word weights) for a request. Definitions come first: a
        definition scores for the words of the request in its name (rare words count for more than words
        found all over the project), more when the whole name is made of words from the request
        (`cmd_index` for "tm index") and most when the name is written exactly. A file scores for its best
        definitions plus words in its path (also inside glued names: "index" in codeindex.py). Test files
        rank below the code they test, unless the request is about tests."""
        self._derive()
        query = [w for w in dict.fromkeys(tokens(text)) if len(w) >= 3 and w not in _STOP]
        wants_tests = any(w in ("test", "tests", "testing", "spec") for w in tokens(text))
        # Only words that look like identifiers are taken as exact names: `parseDuration`, `compute_total`,
        # `Cart.total`. Plain words such as "make" or "print" would match any definition called that.
        written = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.]{3,}", text or "")
                   if re.search(r"[_.]|[a-z][A-Z]|[0-9]", w) or w[0].isupper() and any(c.islower() for c in w[1:])
                   and w.lower() not in _STOP}
        coded = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_.]{2,})\(", text or ""))  # make(), parse(x)
        for chunk in re.findall(r"`([^`\n]{1,80})`", text or ""):                  # `tm index`
            coded.update(re.findall(r"[A-Za-z_][A-Za-z0-9_.]{2,}", chunk))
        written |= {w.lower() for w in coded}
        words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text or "") if w.lower() not in _STOP}
        total = max(len(self.files), 1)
        idf, file_score, defs = {}, defaultdict(float), defaultdict(dict)  # defs[path][(name, line, end, kind)] = score
        for word in query:
            owners = self.word_files.get(word, set())
            glued = set()  # files whose path has the word inside a longer one: index in codeindex
            if len(word) >= 4:
                for token, files in self.path_token_files.items():
                    if len(token) >= len(word) + 3 and word in token:
                        glued |= files
            known = owners | glued
            if not known:
                continue
            idf[word] = weight = math.log(1 + total / len(known))
            for path in glued | {p for p in owners if word in self.path_words[p]}:
                file_score[path] += weight * 1.2
        for word, weight in idf.items():
            for path, line, kind, qualified, end in self.word_defs.get(word, ()):
                bare_tokens = tokens(qualified.rsplit(".", 1)[-1])
                core = [t for t in bare_tokens if t not in _GENERIC_NAME_WORDS] or bare_tokens
                coverage = sum(1 for t in core if t in idf) / len(core)
                key = (qualified, line, end, kind)
                defs[path][key] = defs[path].get(key, 0.0) + weight * (0.4 + 0.6 * coverage) * (
                    0.6 if kind in ("v", "i") else 1.0)
        for name in written:
            for path, line, kind, qualified, end in self.by_name.get(name, []):
                key = (qualified, line, end, kind)
                defs[path][key] = defs[path].get(key, 0.0) + 3.0
        for name in words | written:
            for path in self.stems.get(name, []):  # a file named like a word in the request: invoice -> invoice.py
                file_score[path] += 2.0
        scores = defaultdict(float, file_score)
        for path, found in defs.items():
            best = sorted(found.values(), reverse=True)[:3]
            scores[path] += sum(v * f for v, f in zip(best, (1.0, 0.5, 0.25)))
        for path in list(scores):
            scores[path] += 0.3 * math.log(1 + len(self.rdeps.get(path, [])))  # tie-break: depended-on files
            if not wants_tests and re.search(r"(^|/)(tests?|spec|__tests__)(/|$)|(_test|\.test|\.spec)\.", path):
                scores[path] *= 0.5
        return scores, defs, wants_tests

    def relevant(self, text, limit=8, exclude=()) -> list:
        """[(path, [(definition, line)])] for the files a request most likely concerns, with the
        definitions in them that match best."""
        scores, defs, _ = self._rank(text)
        ranked = sorted((p for p in scores if p not in exclude), key=lambda p: (-scores[p], p))
        if not ranked:
            return []
        top, result = scores[ranked[0]], []
        for path in ranked[:limit]:
            if scores[path] < top * 0.2:  # a hint list should be generous: one strong definition in a big file counts
                break
            best = sorted(defs.get(path, {}).items(), key=lambda kv: (-kv[1], kv[0][1]))[:4]
            result.append((path, [(key[0], key[1]) for key, _ in best]))
        return result

    def best_definitions(self, text, limit=3, exclude=()) -> list:
        """[(path, definition, first line, last line)]: the definitions across the whole project that match
        the request best, so their code can be put in front of the AI instead of making it search."""
        scores, defs, wants_tests = self._rank(text)
        pool = []
        for path, found in defs.items():
            if path in exclude:
                continue
            factor = 0.5 if not wants_tests and re.search(
                r"(^|/)(tests?|spec|__tests__)(/|$)|(_test|\.test|\.spec)\.", path) else 1.0
            for (qualified, line, end, kind), score in found.items():
                span = end - line + 1
                # Code worth handing over: functions and methods, and classes small enough to read at a glance.
                if kind in ("f", "m") and span <= 150 or kind in ("c", "t") and span <= 60:
                    pool.append((score * factor, path, qualified, line, end))
        pool.sort(key=lambda item: (-item[0], item[1], item[3]))
        return [(path, qualified, line, end) for _, path, qualified, line, end in pool[:limit]]

    def excerpt(self, path, line, end, max_lines=45) -> str:
        """The numbered lines of one definition (cut at max_lines), secrets blanked, or ""."""
        from taskmind.secrets import redact
        try:
            with open(os.path.join(str(self.repo.root), path), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return ""
        last = min(end, line + max_lines - 1, len(lines))
        body = "\n".join(f"{n:>5} {lines[n - 1][:200]}" for n in range(line, last + 1))
        more = f"\n      ... ({end - last} more lines; read_file {path} to see them)" if end > last else ""
        return redact(body + more)

    def map_text(self, limit=4000, focus=()) -> str:
        """The most connected files and their main definitions, within `limit` characters."""
        self._derive()
        folders = defaultdict(int)
        for path in self.files:
            parts = path.split("/")
            folders["/".join(parts[:2]) + "/" if len(parts) > 2 else (parts[0] + "/" if len(parts) > 1 else "./")] += 1
        top_folders = sorted(folders.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
        head = f"{len(self.files)} code files. Folders: " + ", ".join(f"{f} ({n})" for f, n in top_folders)

        def importance(path):
            entry = self.files[path]
            boost = 5 if path in focus else 0
            tests = 0.4 if re.search(r"(^|/)(tests?|spec|__tests__)(/|$)|(_test|\.test|\.spec)\.", path) else 1
            return tests * (boost + 2 * len(self.rdeps.get(path, [])) + math.sqrt(len(entry[4])))

        lines, used = [head, "Key files (most imported first), with their main definitions:"], len(head) + 60
        for path in sorted(self.files, key=lambda p: (-importance(p), p)):
            entry = self.files[path]
            if not entry[4] and not self.rdeps.get(path):
                continue
            names = [q for q, k, *_ in entry[4] if k in ("c", "f", "v", "t") and "." not in q][:7]
            users = len(self.rdeps.get(path, []))
            line = f"  {path}" + (f" [imported by {users}]" if users else "") + (": " + ", ".join(names) if names else "")
            if used + len(line) > limit:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)
