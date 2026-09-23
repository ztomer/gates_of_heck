"""Did the working tree move while a gate ran over it?

THE CLASS (ZoneWM 2c.9, 2026-09-21). A gate that runs on the WORKING TREE
certifies whatever bytes were there while it ran. Edit a source file during a
background `swift test` and the run either dies in a target nobody touched
(`error: fatalError`, no cause named) or goes green over a tree that is no
longer the one on disk. Both answers are about a tree that never existed.
The push gate is immune -- it runs on a clean worktree of the pushed commit
(gates/push_gate.sh) -- but every gate run by hand, and every repo's own
`make verify`, is not.

THE FIX. Stamp the tree at the start, compare at the end, and when they
differ say so by name: "the tree moved under the gate", with the paths. A red
run then names its real cause, and a green run over a moving tree is refused
rather than trusted.

WHAT THE STAMP IS. (mtime_ns, size) of every file git would show: tracked
files plus untracked ones that are not ignored (a new source file is compiled
by SwiftPM and cargo alike, so it is part of the tree). Ignored files are the
gate's own output (.build, target/) and are excluded by construction. HEAD
and the index are NOT part of it: a commit during the run leaves the working
tree's bytes where they were, and those bytes are what the gate judged.

Usage:
    tree_stamp.py take  <repo> <stamp-file>
    tree_stamp.py check <repo> <stamp-file> [--name <gate>]

`check` exits 0 when the tree is where it was, 1 when it moved (naming up to
MAX_NAMED paths), 2 when it cannot answer (not a repo, no stamp).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.lib import err  # noqa: E402

# How many moved paths a failure names before summarising the rest.
MAX_NAMED = 10

# Written INTO the tree by the gates themselves: the tree lock (ignored
# globally, not per repo, so git may still list it), coverage data, and the
# lockfiles a build tool writes when a repo has none or resolves afresh
# (found by this repo's rust_gate tests: cargo creating Cargo.lock read as a
# move). A lockfile edited BY HAND mid-run is missed; that trade is taken,
# because the build tool rewriting one is the common case by far.
GATE_OWN_FILES = frozenset({".goh-tree.lock", ".coverage", "Cargo.lock", "Package.resolved"})

# Directories the gate's own TOOLS write into the tree as they run: bytecode,
# test and lint caches, build products. A repo that does not ignore them still
# never has source in them, so a file under one is gate output, not a move.
# (Found by this repo's py_gate test: pytest's __pycache__ in an un-ignored
# package read as "3 files changed" on a still tree.)
GATE_OUTPUT_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".hypothesis", ".build", "target",
})

EXIT_MOVED = 1
EXIT_CANNOT_ANSWER = 2


def listed_files(repo: Path) -> list[str]:
    """Every path git would show: tracked, plus untracked-and-not-ignored."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True, capture_output=True,
    ).stdout
    return sorted(p for p in set(out.decode("utf-8", "surrogateescape").split("\0")) if p and not is_gate_output(p))


def is_gate_output(rel: str) -> bool:
    """A file the gate's own tools write while it runs; see GATE_OUTPUT_DIRS."""
    parts = rel.split("/")
    return parts[-1] in GATE_OWN_FILES or parts[-1].startswith(".coverage.") \
        or any(part in GATE_OUTPUT_DIRS for part in parts[:-1])


def fingerprint(repo: Path) -> dict[str, list[int] | None]:
    """path -> [mtime_ns, size], or None for a tracked file that is absent."""
    stamp: dict[str, list[int] | None] = {}
    for rel in listed_files(repo):
        try:
            st = os.stat(repo / rel, follow_symlinks=False)
        except FileNotFoundError:
            stamp[rel] = None
            continue
        stamp[rel] = [st.st_mtime_ns, st.st_size]
    return stamp


def moved(before: dict, after: dict) -> list[str]:
    """Paths added, removed or changed between two fingerprints, sorted."""
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def take(repo: Path, stamp_file: Path) -> int:
    stamp_file.parent.mkdir(parents=True, exist_ok=True)
    stamp_file.write_text(json.dumps(fingerprint(repo)))
    return 0


def check(repo: Path, stamp_file: Path, name: str) -> int:
    try:
        before = json.loads(stamp_file.read_text())
    except (OSError, ValueError) as exc:
        err(f"{name}: no readable tree stamp at {stamp_file} ({exc}) -- cannot say whether the tree moved")
        return EXIT_CANNOT_ANSWER
    changed = moved(before, fingerprint(repo))
    if not changed:
        return 0
    err(f"{name}: the tree moved under the gate -- {len(changed)} file(s) changed while it ran:")
    for rel in changed[:MAX_NAMED]:
        print(f"    {rel}", file=sys.stderr)
    if len(changed) > MAX_NAMED:
        print(f"    ... and {len(changed) - MAX_NAMED} more", file=sys.stderr)
    err("  its result is about a tree that no longer exists; re-run with the tree still")
    return EXIT_MOVED


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("action", choices=["take", "check"])
    ap.add_argument("repo", type=Path)
    ap.add_argument("stamp", type=Path)
    ap.add_argument("--name", default="gate")
    args = ap.parse_args(argv)
    try:
        if args.action == "take":
            return take(args.repo, args.stamp)
        return check(args.repo, args.stamp, args.name)
    except subprocess.CalledProcessError as exc:
        err(f"{args.name}: cannot list the tree at {args.repo} (git: {exc.stderr.decode().strip()})")
        return EXIT_CANNOT_ANSWER


if __name__ == "__main__":
    sys.exit(main())
