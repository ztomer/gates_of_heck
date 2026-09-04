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

In --staged mode this polices THE INDEX via checks/_gitutil.py.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402

# `<<<<<<< ours` and `>>>>>>> theirs`, and the `|||||||` base marker that diff3
# adds. Anchored to line start; the trailing space/EOL keeps `>>>>>>>` in a doc
# about shell redirection from tripping it.
MARKER = re.compile(rb"^(<{7}|>{7}|\|{7})(\s|$)", re.MULTILINE)


def main() -> int:
    staged = "--staged" in sys.argv
    root = repo_root()
    bad: list[tuple[str, int, str]] = []

    for path in listed_files(root, staged=staged):
        blob = content_bytes(root, path, staged=staged)
        if blob is None or b"\0" in blob[:8000]:  # gone / binary
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
