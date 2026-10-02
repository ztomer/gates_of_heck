#!/usr/bin/env python3
"""check_python_formatted.py — the repo's Python is ruff-formatted, and ruff is here.

WHY. A Python tree with no formatter has its SHAPE decided by whoever last
edited each file by hand, and that is not a tidiness complaint. It is a
correctness one: a hand rewraps a line and merges a comment onto a `continue`,
or de-indents one statement out of its loop, and the result is a file that
still parses and still reads right. Both happened in one session in one repo
(2026-10-01) and neither was found by reading the diff — each surfaced only
because some unrelated gate happened to run afterwards. A tool that owns the
shape makes that class unrepresentable; restraint is only the other half.

SCOPE. Formatting only. Linting is a per-repo policy decision (`ruff check`
trips over hand-written instrumentation in ways a formatter never does), and
coverage belongs to the project's own runner — `gates/py_gate.sh` is the gate
for a repo that has a pytest suite, and it runs all three. This checker is the
piece a repo wants when it has Python but no pytest project: the shape, alone.

    check_python_formatted.py                  # the whole repo, from its root
    check_python_formatted.py tools scripts    # named trees
    check_python_formatted.py --selftest       # a mis-formatted file fails

TWO THINGS THIS REFUSES TO DO. It does not SKIP when `ruff` is absent — a
formatter that is not installed is a missing gate, not a pass, and a gate that
reports success it never earned is the failure mode the house spends its rules
on. And it does not FORMAT: this checker answers a question, and the repo's own
apply command is what changes the tree, so that "the gate went red" and "somebody
edited 100 files" are never the same event.

Run from anywhere inside the repository, because ruff resolves its settings
from the tree it is run in — `ruff.toml`, `pyproject.toml` — and a formatter
whose answer depends on the directory it was launched from is not a gate.

Calibrated: --selftest plants a mis-formatted file and a formatted one beside
it and requires the first red and the second green. A gate that has never
refused anything has never been watched working.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(
    0,
    os.environ.get(
        "GOH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)
from tui.lib import err, ok  # noqa: E402

REPO = Path.cwd()


def repo_root():
    """The repository root, so the settings file that applies is the repo's own."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return REPO
    top = out.stdout.strip()
    return Path(top).resolve() if out.returncode == 0 and top else REPO


def check(root, paths):
    """(exit code, output) for `ruff format --check` over `paths`, run at `root`."""
    out = subprocess.run(
        ["ruff", "format", "--check", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return out.returncode, (out.stdout + out.stderr).strip()


def selftest(root):
    """The gate must refuse a mis-formatted file and pass a formatted one.

    Both files sit in a temp directory OUTSIDE the tree, so the repo's own
    excludes cannot decide the answer: a gate that went green because its
    settings skipped the fixture has proved nothing.
    """
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d, "bad.py")
        good = Path(d, "good.py")
        bad.write_text("x = {  'a':1,'b':2 }\n")
        good.write_text('x = {"a": 1, "b": 2}\n')
        red, _ = check(d, [str(bad)])
        green, _ = check(d, [str(good)])
    if red == 0:
        err(
            "selftest: a mis-formatted file PASSED — this gate cannot see the defect it exists for"
        )
        return 1
    if green != 0:
        err(
            "selftest: a formatted file FAILED — this gate refuses correct code. "
            f"output was: {green}"
        )
        return 1
    ok(
        "check_python_formatted selftest: a mis-formatted file is caught and a formatted one passes"
    )
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "trees", nargs="*", default=["."], help="trees to check (default: the repo)"
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    if args.selftest:
        return selftest(root)

    if not shutil.which("ruff"):
        err(
            "ruff is not installed, so nothing can check the shape of the Python here — "
            "`brew install ruff` (or `uv tool install ruff`). A missing formatter is a "
            "missing gate, not a pass."
        )
        return 1

    code, out = check(root, args.trees or ["."])
    if code != 0:
        for line in out.splitlines():
            err(f"  {line}")
        err(
            f"{', '.join(args.trees)} is not ruff-formatted. Run your repo's fmt target "
            "(`ruff format`) — do not hand-fix the shape."
        )
        return 1
    ok(f"every file under {', '.join(args.trees)} is ruff-formatted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
