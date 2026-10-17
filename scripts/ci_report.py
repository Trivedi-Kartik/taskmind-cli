"""Turn unittest failures into GitHub annotations (visible without logging in).

    python -m unittest discover -s tests -v 2> out.txt || python scripts/ci_report.py out.txt
"""

import re
import sys


def blocks(text):
    parts = re.split(r"^=+\n(?=(?:FAIL|ERROR): )", text, flags=re.M)
    return [p for p in parts if p.startswith(("FAIL:", "ERROR:"))]


def main(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    found = blocks(text)
    for block in found[:15]:
        head, _, body = block.partition("\n")
        body = body.split("\n" + "-" * 70)[0] if ("-" * 70 + "\n") in body[:80] else body
        lines = [l for l in body.strip().splitlines() if l.strip() and not set(l) <= {"-", "="}]
        detail = "\n".join(lines[-12:])
        message = detail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title={head.strip()[:200]}::{message[:3000]}")
    print(f"{len(found)} failing test(s) reported as annotations.")


if __name__ == "__main__":
    main(sys.argv[1])
