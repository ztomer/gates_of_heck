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
    check_python_formatted.py --staged         # the staged .py files' INDEX blobs
    check_python_formatted.py --selftest       # a mis-formatted file fails

STAGED. Pre-commit used to skip this step on the theory that a formatter's
verdict is a property of the whole tree and a hook that REFORMATTED the
repository would be worse than one that waits. Both halves were true and
neither was the question: `--staged` reformats nothing, and it judges only the
files the commit touches, so a commit is refused for its own bytes and nothing
else. The cost of the old rule was measured -- two unformatted test files passed
pre-commit and refused the v0.20.0 push. Each staged blob goes to
`ruff format --check --force-exclude --stdin-filename <path> -`: ruff resolves the
settings that apply to <path> (so the verdict matches the full run), and
`--force-exclude` keeps the repo's own excludes in force for a named path.

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

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(
    0,
    os.environ.get("GOH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
from tui.lib import err, info, ok  # noqa: E402

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


def _python_files(root, tree):
    """The Python files a scanner would really SEE under `tree`.

    Git's WORKTREE, not `rglob`. This is the hole `check_empty_scope.py`
    exists to catch, and it caught it here: over its empty skeleton it
    reported "every file under . is ruff-formatted" about the gate directory
    it had copied in -- untracked and git-ignored, therefore not part of the
    repository, but plainly on disk for any filesystem walk to find. A
    formatter that checks files the repo does not contain is reporting on
    something other than the repo.

    `listed_files` is tracked + untracked minus ignored, so an empty tree
    yields none and the emptiness test below can say so.
    """
    from _gitutil import listed_files

    try:
        listed = listed_files(str(root), staged=False)
    except (OSError, RuntimeError):
        return []
    prefix = "" if tree in (".", "") else tree.rstrip("/") + "/"
    return [f for f in listed if f.startswith(prefix) and f.endswith(".py")]


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


def _check_blob(root, rel, blob):
    """(rel, exit code, output) for one staged blob, judged as if it lived at `rel`."""
    out = subprocess.run(
        ["ruff", "format", "--check", "--force-exclude", "--stdin-filename", rel, "-"],
        cwd=root,
        input=blob,
        capture_output=True,
        check=False,
    )
    text = (out.stdout + out.stderr).decode("utf-8", "replace").strip()
    return rel, out.returncode, text


def check_staged(root):
    """(staged .py paths, [(rel, output)] of the misformatted ones), from the INDEX.

    One ruff per blob (ruff reads one stdin file), run concurrently: a commit
    touching 30 Python files costs about one ruff start, not thirty in a row.
    """
    from concurrent.futures import ThreadPoolExecutor

    from _gitutil import content_bytes, listed_files

    def judge(rel):
        blob = content_bytes(str(root), rel, staged=True)
        return None if blob is None else _check_blob(root, rel, blob)

    staged = [f for f in listed_files(str(root), staged=True) if f.endswith(".py")]
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(staged)))) as pool:
        results = [r for r in pool.map(judge, staged) if r is not None]
    return [rel for rel, _, _ in results], [(rel, text) for rel, code, text in results if code]


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
        err("selftest: a mis-formatted file PASSED — this gate cannot see the defect it exists for")
        return 1
    if green != 0:
        err(
            "selftest: a formatted file FAILED — this gate refuses correct code. "
            f"output was: {green}"
        )
        return 1
    ok("check_python_formatted selftest: a mis-formatted file is caught and a formatted one passes")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "trees", nargs="*", default=["."], help="trees to check (default: the repo)"
    )
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument(
        "--staged", action="store_true", help="judge the staged .py files' index blobs"
    )
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

    if args.staged:
        paths, bad = check_staged(root)
        if not paths:
            info("not applicable: no staged Python files -- nothing for the formatter to judge")
            return 0
        if bad:
            for rel, text in bad:
                err(f"  {rel}: {text.splitlines()[0] if text else 'would reformat'}")
            err(
                f"{len(bad)} of {len(paths)} staged Python file(s) are not ruff-formatted. "
                "Run your repo's fmt target (`ruff format`) and re-stage -- do not hand-fix "
                "the shape."
            )
            return 1
        ok(f"every staged Python file ({len(paths)}) is ruff-formatted")
        return 0

    # No Python in the repository is a NAMED NON-RUN, not a pass: "every file
    # under . is ruff-formatted" over zero files is vacuously true and reads
    # exactly like compliance.
    if not _python_files(root, (args.trees or ["."])[0]):
        info(
            f"not applicable: no Python files under {', '.join(args.trees or ['.'])} "
            "-- nothing for the formatter to have an opinion about"
        )
        return 0

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
