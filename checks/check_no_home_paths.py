#!/usr/bin/env python3
"""Fail if any git-tracked text file hard-codes a path under someone's HOME.

    /Users/<name>/…   /home/<name>/…   ~/Projects/…   $HOME/Projects/…

A shipped binary, a script, a shell function or a doc that names a checkout
by its absolute location works on exactly one machine at exactly one
moment. The `salary` CLI failed for two weeks after its repos moved because
`~/.zshrc` passed `--config-dir ~/Projects/salary`; ZincTender's Swift
bridge carries four `/Users/ztomer/…` dylib candidates; its packaging
script names `$HOME/Projects/Finance/…` twice. Each was found by a user,
not a gate. This gate is the class: derive locations from the executable,
the repo root, an env var or a bundle — never from a literal home path.

    python3 checks/check_no_home_paths.py                     # all tracked text files
    python3 checks/check_no_home_paths.py --staged            # the index (pre-commit)
    python3 checks/check_no_home_paths.py --exclude '^docs/'  # regex on repo paths

A path that must stand — a dev-only fallback, a recorded incident — is
suppressed with a marker carrying a reason, on the line or the line above:

    let dev = "/Users/me/src/x";  // path-ok: dev fallback, never reached in a bundle

A bare `path-ok:` with no reason suppresses nothing.

Opt-in per repo (GOH_NO_HOME_PATHS=1 in .gatesrc): turning it on
everywhere at once would go red across every repo that ever wrote a
tilde into a README, and a gate that lands red in twenty places is a gate
that gets disabled. Each repo turns it on when its tree is clean or its
violations carry reasons.
"""
from __future__ import annotations  # OS python3 may be 3.9

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402

PATTERNS = (
    ("macOS home path", re.compile(r"(?<![\w.])/Users/[A-Za-z0-9_.-]+/")),
    ("linux home path", re.compile(r"(?<![\w.])/home/[A-Za-z0-9_.-]+/")),
    ("tilde checkout path", re.compile(r"(?<![\w])~/Projects/")),
    ("HOME checkout path", re.compile(r"\$\{?HOME\}?/Projects/")),
)

MARKER = re.compile(r"path-ok:\s*\S")


def _suppressed(prev_line: str | None, line: str) -> bool:
    return bool(MARKER.search(line) or (prev_line and MARKER.search(prev_line)))


def _findings(line: str) -> list[tuple[int, str]]:
    return [(m.start() + 1, kind) for kind, pat in PATTERNS for m in pat.finditer(line)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--exclude", default="",
                    help="regex; matching repo-relative paths are skipped")
    args = ap.parse_args()
    skip = re.compile(args.exclude) if args.exclude else None

    root = repo_root()
    if not root:
        print("[no_home_paths] not a git repo — skipping")
        return 0

    bad = []
    checked = 0
    for f in listed_files(root, staged=args.staged):
        if skip and skip.search(f):
            continue
        blob = content_bytes(root, f, staged=args.staged)
        if blob is None:
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        checked += 1
        prev = None
        for lineno, line in enumerate(text.splitlines(), 1):
            if not _suppressed(prev, line):
                for col, kind in _findings(line):
                    bad.append(f"{f}:{lineno}:{col}: {kind}")
            prev = line

    scope = "staged" if args.staged else "tracked"
    if bad:
        print(f"✗ HARD-CODED HOME PATH in {len(bad)} location(s) ({scope}) — derive it from "
              f"the executable, the repo root, an env var or the bundle; a path that must "
              f"stand carries `path-ok: <reason>` on the line or above:")
        for b in bad[:200]:
            print("  " + b)
        if len(bad) > 200:
            print(f"  … and {len(bad) - 200} more")
        return 1
    if checked == 0:
        # A checker that reports compliance over nothing is the empty-scope
        # failure the structural gate already polices elsewhere; say so.
        print(f"✗ [no_home_paths] nothing to check ({scope}) — refusing to report clean over zero files")
        return 1
    print(f"✓ [no_home_paths] OK — {checked} {scope} files clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
