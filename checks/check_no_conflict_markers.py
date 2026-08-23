#!/usr/bin/env python3
"""Fail if any tracked text file contains a merge-conflict marker.

A conflict marker that reaches a commit is a merge somebody walked away from
half-finished. It is cheap to check and it has escaped into a release before.

Only a marker at the START of a line counts, and `=======` is deliberately NOT
one of them on its own: it is ordinary Markdown/reStructuredText underlining.
The real signal is the `<<<<<<< ` / `>>>>>>> ` pair, which carries a branch
name after the marker.

    check_no_conflict_markers.py            # every tracked file
    check_no_conflict_markers.py --staged   # staged files only
"""

import re
import subprocess
import sys

# `<<<<<<< ours` and `>>>>>>> theirs`, and the `|||||||` base marker that diff3
# adds. Anchored to line start; the trailing space/EOL keeps `>>>>>>>` in a doc
# about shell redirection from tripping it.
MARKER = re.compile(rb"^(<{7}|>{7}|\|{7})(\s|$)", re.MULTILINE)


def _files(staged: bool) -> list[str]:
    cmd = (
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
        if staged
        else ["git", "ls-files"]
    )
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def main() -> int:
    staged = "--staged" in sys.argv
    bad: list[tuple[str, int, str]] = []

    for path in _files(staged):
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
        except (OSError, IsADirectoryError):
            continue
        if b"\0" in blob[:8000]:  # binary
            continue
        for m in MARKER.finditer(blob):
            line_no = blob.count(b"\n", 0, m.start()) + 1
            line = blob[m.start() : blob.find(b"\n", m.start())]
            bad.append((path, line_no, line.decode("utf-8", "replace")[:80]))

    if bad:
        print("✗ [no_conflict_markers] merge conflict markers found:", file=sys.stderr)
        for path, line_no, text in bad:
            print(f"    {path}:{line_no}: {text}", file=sys.stderr)
        return 1

    scope = "staged" if staged else "tracked"
    print(f"→ [no_conflict_markers] OK — no markers in {scope} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
