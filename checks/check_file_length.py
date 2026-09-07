#!/usr/bin/env python3
"""Fail if any tracked source file exceeds a line cap.

One check, one name. This replaces the three that had drifted apart across
repos (check_file_length / check_loc / check_file_size), each with its own
limit and its own idea of what counts as source.

The cap is a design gate, not a style gate: past a few hundred lines a file
has stopped being one thing, and the split is the fix. Vendored code, generated
code and lockfiles are not yours to split, so exclude them via --exclude.

    check_file_length.py --max 500
    check_file_length.py --max 500 --exclude 'third_party/|\\.generated\\.'
    check_file_length.py --max 500 --staged

In --staged mode this polices THE INDEX via checks/_gitutil.py.
"""

import argparse
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, line_count, listed_files, repo_root  # noqa: E402

# Files the cap is about. Data, docs and lockfiles are legitimately long.
SOURCE_SUFFIXES = (
    ".rs", ".py", ".swift", ".c", ".h", ".cpp", ".hpp", ".cc", ".m", ".mm",
    ".kt", ".java", ".go", ".ts", ".tsx", ".js", ".jsx", ".sh", ".bash", ".rb",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, required=True)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--staged", action="store_true")
    args = ap.parse_args()

    skip = re.compile(args.exclude) if args.exclude else None
    root = repo_root()
    over: list[tuple[str, int]] = []
    checked = 0

    for path in listed_files(root, staged=args.staged):
        if not path.endswith(SOURCE_SUFFIXES):
            continue
        if skip and skip.search(path):
            continue
        blob = content_bytes(root, path, staged=args.staged)
        if blob is None:
            continue
        checked += 1
        n = line_count(blob)
        if n > args.max:
            over.append((path, n))

    if over:
        print(
            f"✗ [file_length] {len(over)} file(s) over the {args.max}-line cap:",
            file=sys.stderr,
        )
        for path, n in sorted(over, key=lambda x: -x[1]):
            print(f"    {n:>6} lines  {path}  (+{n - args.max})", file=sys.stderr)
        print(
            "\n  Split them. If a file genuinely cannot be split (vendored or\n"
            "  generated), add it to GOH_LINE_EXCLUDE in .gatesrc — with a reason.",
            file=sys.stderr,
        )
        return 1

    print(f"→ [file_length] OK — {checked} file(s) within {args.max} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
