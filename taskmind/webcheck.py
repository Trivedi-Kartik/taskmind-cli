"""Check a web page the way a visitor would meet it: open it in a hidden browser.

A test that only proves files exist says nothing about whether the page works, so
for web projects TaskMind checks the page itself:
  - every local file it links to (styles, scripts, images) exists,
  - it opens in a real browser (Chrome, Chromium or Edge, hidden) without JavaScript errors,
  - it shows some text.

Without a browser, only the file links are checked and the result says so.
"""

import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

BROWSERS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
            "msedge", "chrome"]
WINDOWS_BROWSERS = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"]
MAC_BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
REMOTE = re.compile(r"^([a-z][a-z0-9+.-]*:|//|#)", re.I)  # http:, data:, mailto:, //cdn, #anchor


SKIP = {"node_modules", ".git", "dist", "build", "vendor", "coverage", ".venv", "venv"}


def pages(root) -> list:
    """The project's web pages, not test pages: the HTML files at the top, or one folder down
    (e.g. public/ or site/) when there are none at the top."""
    root = Path(root)
    found = [p for p in root.glob("*.html") if "test" not in p.stem.lower()]
    if not found:
        found = [p for p in root.glob("*/*.html") if "test" not in p.stem.lower() and p.parent.name not in SKIP]
    return sorted(found)


def is_web_project(root) -> bool:
    return bool(pages(root))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in ("src", "href"):
            value = (attrs.get(name) or "").strip()
            if value and not (tag == "a" and name == "href"):  # page links may point anywhere
                self.links.append(value)


def missing_files(page) -> list:
    parser = _Links()
    parser.feed(Path(page).read_text(encoding="utf-8", errors="replace"))
    missing = []
    for link in parser.links:
        if REMOTE.match(link):
            continue
        target = link.split("#")[0].split("?")[0]
        if target and not (Path(page).parent / target).exists():
            missing.append(target)
    return missing


def find_browser():
    for name in BROWSERS:
        path = shutil.which(name)
        if path:
            return path
    if os.name == "nt":
        for base in filter(None, (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                                  os.environ.get("LOCALAPPDATA"))):
            for rel in WINDOWS_BROWSERS:
                if (Path(base) / rel).exists():
                    return str(Path(base) / rel)
    for path in MAC_BROWSERS:
        if os.path.exists(path):
            return path
    return None


_CONSOLE = re.compile(r':CONSOLE[^\]]*\] "(.*)", source: (\S+) \((\d+)\)')


def open_in_browser(browser, page, timeout=60) -> tuple:
    """Load the page in a hidden browser. Returns (JavaScript errors, visible text)."""
    profile = tempfile.mkdtemp(prefix="taskmind-browser-")  # never touch the user's own browser profile
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--enable-logging=stderr", "--v=0", f"--user-data-dir={profile}", "--virtual-time-budget=5000",
           "--dump-dom", Path(page).resolve().as_uri()]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")  # Chrome refuses to run as root otherwise
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    errors = []
    for line in result.stderr.splitlines():
        match = _CONSOLE.search(line)
        if match and ("Uncaught" in match.group(1) or ":ERROR:" in line):
            errors.append(f"{match.group(1)} ({Path(match.group(2)).name} line {match.group(3)})")
    body = re.search(r"<body[^>]*>(.*)</body>", result.stdout, re.S | re.I)
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", body.group(1) if body else "",
                  flags=re.S | re.I)
    return list(dict.fromkeys(errors)), " ".join(text.split())


def check(root, browser=None) -> dict:
    """{"problems": [...], "browser": bool, "pages": [...]} for the project's web pages."""
    root = Path(root)
    found = pages(root)
    browser = browser if browser is not None else find_browser()
    problems = []
    for page in found:
        name = page.relative_to(root).as_posix()
        for target in missing_files(page):
            problems.append(f"{name} links to {target}, which doesn't exist")
        if browser:
            try:
                errors, text = open_in_browser(browser, page)
            except (OSError, subprocess.SubprocessError) as exc:
                problems.append(f"Couldn't open {name} in the browser: {exc}")
                continue
            problems += [f"{name}: JavaScript error: {e}" for e in errors]
            if not text:
                problems.append(f"{name} shows no text when opened in a browser")
    return {"problems": problems, "browser": bool(browser), "pages": [p.relative_to(root).as_posix() for p in found]}
