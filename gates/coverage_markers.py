"""cov:ignore marker handling for the Swift coverage gate.

Split out of gates/coverage_swift.py, which grew past the house 500-line
cap. This half answers one question -- which source lines the repo has
explicitly excused, and whether that excuse is growing -- and shares nothing
with the binary discovery and llvm-cov processing that remain there.

A marker without a reason, or a start without an end, is a gate error rather
than a silent forgiveness: an exclusion nobody has to justify is not an
exclusion, it is a hole.
"""

import glob
import json
import os
import re
import sys

SINGLE = re.compile(r"//\s*cov:ignore\b(?!-)")
START = re.compile(r"//\s*cov:ignore-start\b")
END = re.compile(r"//\s*cov:ignore-end\b")

TEST_DIR = re.compile(r"(^|/)(Tests|.*Tests)/")

def parse_markers(lines):
    """(excluded {1-indexed line}, errors [str]) from one source file."""
    excluded = set()
    errors = []
    open_at = None
    in_block = False
    for i, line in enumerate(lines, start=1):
        if START.search(line):
            reason = re.search(r"cov:ignore-start\s*:\s*(.+?)\s*$", line)
            if not reason or not reason.group(1).strip():
                errors.append(f"{i}: cov:ignore-start without a reason")
            open_at = i
            in_block = True
            excluded.add(i)
        elif END.search(line):
            excluded.add(i)
            in_block = False
            open_at = None
        elif SINGLE.search(line):
            reason = re.search(r"cov:ignore\s*:\s*(.+?)\s*$", line)
            if not reason or not reason.group(1).strip():
                errors.append(f"{i}: cov:ignore without a reason")
            else:
                excluded.add(i)
        elif in_block:
            excluded.add(i)
    if open_at is not None:
        errors.append(f"{open_at}: unclosed cov:ignore-start block")
    return excluded, errors

def find_exclusions(proj, ignore_re, include_re=""):
    """({abspath: excluded lines}, [errors]) over non-test Swift sources."""
    out = {}
    errors = []
    for path in sorted(glob.glob(os.path.join(proj, "**", "*.swift"),
                                 recursive=True)):
        rel = os.path.relpath(path, proj)
        if TEST_DIR.search("/" + rel):
            continue
        if include_re and not re.search(include_re, path):
            continue
        if ignore_re and re.search(ignore_re, path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        excluded, errs = parse_markers(lines)
        for e in errs:
            errors.append(f"{rel}:{e}")
        if excluded:
            out[path] = excluded
    return out, errors

def adjust_coverage(raw_total, raw_covered, counts, excluded):
    """(pct, forgiven): drop only UNCOVERED marker lines from the total."""
    forgiven = sum(1 for ln in excluded if counts.get(ln) == 0)
    adj_total = raw_total - forgiven
    pct = (100.0 * raw_covered / adj_total) if adj_total else 0.0
    return pct, forgiven

def check_marker_ceiling(forgiven, ceiling_path):
    """Shrink-only ceiling (ZeroThunder). forgiven > max => fail."""
    if not ceiling_path or not os.path.exists(ceiling_path):
        return 0
    try:
        max_forgiven = int(json.load(open(ceiling_path, encoding="utf-8"))["max_forgiven_lines"])
    except Exception as exc:
        print(f"✗ [coverage] cannot read ceiling {ceiling_path}: {exc}", file=sys.stderr)
        sys.exit(2)
    if forgiven > max_forgiven:
        print(f"✗ [coverage] FORGIVENESS GREW: {forgiven} > ceiling {max_forgiven}.", file=sys.stderr)
        sys.exit(1)
    if forgiven < max_forgiven:
        print(f"→ [coverage] forgiveness fell to {forgiven} (ceiling {max_forgiven}) — lower it.")
    return 0
