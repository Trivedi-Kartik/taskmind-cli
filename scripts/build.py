"""Build TaskMind for distribution.

    python3 scripts/build.py          # sync the bundled guide + build dist/taskmind.pyz
    python3 scripts/build.py --check  # only check the bundled guide matches README.md (for CI)

The wheel and sdist for PyPI are built with the standard tool once it's installed:
    python3 -m pip install build && python3 -m build
"""

import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "taskmind" / "guide.md"


def sync_guide(check=False) -> bool:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if GUIDE.exists() and GUIDE.read_text(encoding="utf-8") == readme:
        return True
    if check:
        print("taskmind/guide.md is out of date: run python3 scripts/build.py")
        return False
    GUIDE.write_text(readme, encoding="utf-8")
    print("Updated taskmind/guide.md from README.md")
    return True


def build_pyz() -> Path:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / "taskmind.pyz"
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(ROOT / "taskmind", Path(tmp) / "taskmind",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        zipapp.create_archive(tmp, target, interpreter="/usr/bin/env python3",
                              main="taskmind.cli:main", compressed=True)
    print(f"Built {target} ({target.stat().st_size // 1024} KB). Run it with: python3 {target} --help")
    return target


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if sync_guide(check=True) else 1)
    sync_guide()
    build_pyz()
