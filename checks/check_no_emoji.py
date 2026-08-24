#!/usr/bin/env python3
"""Fail if any git-tracked text file contains a disallowed emoji.

Policy: emoji are a FAILURE STATE. The only permitted pictographic/symbol
glyphs are the Susan-Kare icon set (see `tui/stylerc`) plus the plain typographic
arrows used as text operators:

    →  U+2192  (ICON_START)        ✓  U+2713  (ICON_OK)
    ✗  U+2717  (ICON_ERR)          ⚠  U+26A0  (ICON_WARN)
    ↔  U+2194  ↑  U+2191  ↓  U+2193 (typographic arrows — permitted as text)

Everything else in the emoji/symbol ranges below (check-mark-button, colour squares, the warn sign
with an emoji variation-selector, decorative section emoji, double-arrow / star, keycaps, regional
flags, ...) is rejected. This is a deterministic, app-free style gate, run by `ci_local.sh` and the
pre-commit hook so the policy can't silently regress.

    python3 tools/check_no_emoji.py                        # all tracked text files
    python3 tools/check_no_emoji.py --staged               # staged files only (pre-commit hook)
    python3 tools/check_no_emoji.py --exclude '^vendor/'   # skip vendored trees

Exclusions come from --exclude (a regex on repo-relative paths), wired from
GOH_EXCLUDE in .gatesrc by gates/structural.sh — per-repo policy never lives
in this shared source file.

In --staged mode this polices THE INDEX (what will be committed), via
checks/_gitutil.py — not the working tree.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402


# The complete allow-list, in two buckets so the policy is auditable:
#   1. Kare icon set + approved typographic arrows — the canonical vocabulary.
#   2. Functional (non-emoji) symbols that carry meaning, not decoration: Mac modifier-key glyphs
#      shown in native menus / shortcut docs, and the left-arrow companion to the cardinal set.
# To go Kare-strict, delete bucket 2 (and the three arrows from bucket 1).
# ORDERED matters: this tuple is also the failure message's permit list, so
# the policy and what users are told cannot drift apart. Membership tests use
# the set built from it.
ALLOWED_ORDERED = (
    "→", "✓", "✗", "⚠", "↔", "↑", "↓",   # 1. Kare icons + arrows
    "←", "⌘", "⌥", "⌨",                    # 2. functional: cardinal arrow + Mac keys (⌘ cmd / ⌥ opt)
)
ALLOWED = frozenset(ALLOWED_ORDERED)

# Codepoint ranges that hold emoji / decorative pictographs. A char in any of these that is NOT in
# ALLOWED is a failure. (inclusive lo, inclusive hi)
RANGES = (
    (0x1F000, 0x1FAFF),   # all emoji blocks: pictographs, symbols, supplemental, regional flags
    (0x2600,  0x26FF),    # misc symbols (sun, gear, no-entry, ... and the allowed warn sign U+26A0)
    (0x2700,  0x27BF),    # dingbats (check-mark-button, scissors, ... and the allowed check/x)
    (0x2300,  0x23FF),    # misc technical (pause, stopwatch, ... and the allowed keyboard glyph)
    (0x2B00,  0x2BFF),    # stars, big block arrows
    (0x2190,  0x21FF),    # arrows (cardinal + bidi allowed via ALLOWED; double-arrow, mapsto rejected)
    (0xFE00,  0xFE0F),    # variation selectors (emoji-presentation VS16, etc.)
    (0x20E3,  0x20E3),    # combining enclosing keycap
)


def _is_disallowed(ch: str) -> bool:
    if ch in ALLOWED:
        return False
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in RANGES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--exclude", default="",
                    help="regex; matching repo-relative paths are skipped")
    args = ap.parse_args()

    import re
    skip = re.compile(args.exclude) if args.exclude else None

    root = repo_root()
    if not root:
        print("[no_emoji] not a git repo — skipping")
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
        for lineno, line in enumerate(text.splitlines(), 1):
            for col, ch in enumerate(line, 1):
                if _is_disallowed(ch):
                    bad.append(f"{f}:{lineno}:{col}: U+{ord(ch):04X} {ch!r}")

    if bad:
        scope = "staged" if args.staged else "tracked"
        print(f"✗ DISALLOWED EMOJI in {len(bad)} location(s) ({scope}) — "
              f"only the Kare icon set is permitted "
              f"({' '.join(ALLOWED_ORDERED)}):")
        for b in bad[:200]:
            print("  " + b)
        if len(bad) > 200:
            print(f"  … and {len(bad) - 200} more")
        return 1
    scope = "staged" if args.staged else "tracked"
    print(f"✓ [no_emoji] OK — {checked} {scope} files clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
