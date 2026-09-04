#!/usr/bin/env python3
"""Fail if any git-tracked text file contains a committed secret.

One committed token dwarfs every other defect class these gates cover, so
this gate exists — but its scope is DELIBERATELY narrow: known
high-confidence prefixes plus private-key headers. No entropy heuristics:
an unproven heuristic cries wolf, gets switched off, and is worse than a
narrow gate that never does. Entropy detection is recorded followup work,
not a TODO in this file.

    python3 checks/check_no_secrets.py                        # all tracked text files
    python3 checks/check_no_secrets.py --staged               # staged files only (pre-commit hook)
    python3 checks/check_no_secrets.py --exclude '^vendor/'   # skip vendored trees

A revoked test vector or documented example is suppressed with a marker
carrying a reason, on the offending line or the line above:

    TOKEN = "ghp_..."  # secret-ok: revoked 2026-01-01, stands as a vector

A bare marker without reason text suppresses nothing — an escape hatch
that works empty is not an escape hatch, it is an off switch.

In --staged mode this polices THE INDEX (what will be committed), via
checks/_gitutil.py — not the working tree.
"""
from __future__ import annotations  # OS python3 may be 3.9: `X | Y` must not evaluate at def time

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402


# (kind, pattern). Tails are length-gated so prose cannot trip them: a bare
# prefix without key-length material after it is not a finding.
PATTERNS = (
    ("github token", re.compile(r"\bghp_[A-Za-z0-9]{36,}")),
    ("github oauth token", re.compile(r"\bgho_[A-Za-z0-9]{36,}")),
    ("github fine-grained pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("anthropic api key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}")),
    ("openai project key", re.compile(r"\bsk-proj-[A-Za-z0-9\-_]{20,}")),
    ("openai api key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("slack token", re.compile(r"\bxox[bpas]-[A-Za-z0-9\-]{10,}")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
)

# Suppression marker with a MANDATORY reason (non-whitespace after the colon).
MARKER = re.compile(r"secret-ok:\s*\S")


def _suppressed(prev_line: str | None, line: str) -> bool:
    return bool(MARKER.search(line) or (prev_line and MARKER.search(prev_line)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--exclude", default="",
                    help="regex; matching repo-relative paths are skipped")
    args = ap.parse_args()

    skip = re.compile(args.exclude) if args.exclude else None

    root = repo_root()
    if not root:
        print("[no_secrets] not a git repo — skipping")
        return 0

    files = listed_files(root, staged=args.staged)
    bad = []
    checked = 0
    for f in files:
        if skip and skip.search(f):
            continue
        blob = content_bytes(root, f, staged=args.staged)
        if blob is None:
            continue  # deleted / unreadable — nothing to police
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary — no text to police
        checked += 1
        prev = None
        for lineno, line in enumerate(text.splitlines(), 1):
            if not _suppressed(prev, line):
                for col, ch_kind in _findings(line):
                    bad.append(f"{f}:{lineno}:{col}: {ch_kind}")
            prev = line

    if bad:
        scope = "staged" if args.staged else "tracked"
        print(f"✗ DISALLOWED SECRET in {len(bad)} location(s) ({scope}) — "
              f"rotate the credential; revoked vectors use "
              f"`secret-ok: <reason>` on the line or above:")
        for b in bad[:200]:
            print("  " + b)
        if len(bad) > 200:
            print(f"  … and {len(bad) - 200} more")
        return 1
    scope = "staged" if args.staged else "tracked"
    print(f"✓ [no_secrets] OK — {checked} {scope} files clean")
    return 0


def _findings(line: str) -> list[tuple[int, str]]:
    out = []
    for kind, pat in PATTERNS:
        for m in pat.finditer(line):
            out.append((m.start() + 1, kind))
    return out


if __name__ == "__main__":
    sys.exit(main())
