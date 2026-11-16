"""The release land's gate: a diff of release files only re-runs the checks that read them.

GOH.SHIPRELREUSE R3 (coordinator ruling 2026-11-15, owner-delegated: a ship at 10 min or less).
tools/release_goh.sh lands a commit that differs from the land-gated tip it was cut from only in
CHANGELOG.md and the changelog/ fragments it folds (R1: the version is the tag). Its land ran the
whole push gate again over bytes that gate had just judged. Now the kit hands tools/land.sh this as
its gate, with the tip it was cut from:

    release_gate.py run --base TIP --full CMD   # the land's gate (GOH_LAND_GATE), in the worktree
    release_gate.py decide --base TIP           # prints `release` or `full`
    release_gate.py names                       # the tests that read the release files

`run` diffs TIP against HEAD. Every path CHANGELOG.md or under changelog/ (and at least one): it
runs the structural gate (`gates/structural.sh --full`: every check over every tracked file, the
changelog's included) and then the tests `names` finds. Anything else -- one source file, an empty
diff, a TIP that is no ancestor because main moved and land.sh rebased -- runs CMD, the full gate,
unchanged. The named set is a probe, never a list: a test file in tests/ whose text names
CHANGELOG.md, the changelog/ directory or a "changelog" path part, so a new reader joins it by
being written. Pinned by tests/test_release_gate.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

RELEASE_FILE = "CHANGELOG.md"
RELEASE_DIR = "changelog/"
READS_RELEASE_FILES = re.compile(r"CHANGELOG\.md|changelog/|[\"']changelog[\"']")
STRUCTURAL = "./gates/structural.sh --full"
RUNNER = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider -n auto --dist loadgroup"


def names(root: Path) -> list[str]:
    """The tests that read a release file, relative to root: the probe, re-run at every release."""
    return [
        f"tests/{p.name}"
        for p in sorted((root / "tests").glob("test_*.py"))
        if READS_RELEASE_FILES.search(p.read_text(encoding="utf-8"))
    ]


def release_only(paths: list[str]) -> bool:
    """True for a non-empty diff of CHANGELOG.md and changelog/ fragments and nothing else."""
    return bool(paths) and all(p == RELEASE_FILE or p.startswith(RELEASE_DIR) for p in paths)


def changed(root: Path, base: str) -> list[str] | None:
    """The paths HEAD changed since base, or None when base is no ancestor of HEAD (main moved)."""
    git = ["git", "-C", str(root)]
    if subprocess.run([*git, "merge-base", "--is-ancestor", base, "HEAD"], check=False,
                      stdin=subprocess.DEVNULL, capture_output=True).returncode != 0:  # fmt: skip
        return None
    diff = [*git, "diff", "--no-renames", "--name-only", base, "HEAD"]
    out = subprocess.run(diff, check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line]


def decide(root: Path, base: str) -> str:
    paths = changed(root, base)
    return "release" if paths is not None and release_only(paths) else "full"


def _sh(cmd: str, root: Path) -> int:
    """CMD in root, without GOH_LAND_GATE: the key that chose this gate is no configuration of the
    gates it runs, and every GOH_* is in a proven step's cache identity (gates/_rust_proven.sh)."""
    env = {k: v for k, v in os.environ.items() if k != "GOH_LAND_GATE"}
    return subprocess.run(
        ["bash", "-c", cmd], cwd=root, env=env, stdin=subprocess.DEVNULL, check=False
    ).returncode


def run(root: Path, base: str, full: str, structural: str, runner: str) -> int:
    if decide(root, base) == "full":
        print(f"release gate: the diff since {base[:8]} is not release files only -- the full gate")
        sys.stdout.flush()
        return _sh(full, root)
    tests = names(root)
    print(f"release gate: release files only since {base[:8]} -- structural + {len(tests)} tests")
    sys.stdout.flush()
    rc = _sh(structural, root)
    if rc != 0:
        return rc
    if not tests:
        return 0
    return _sh(f"{runner} {' '.join(shlex.quote(t) for t in tests)}", root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("run", "decide", "names"):
        p = sub.add_parser(verb)
        p.add_argument("--root", type=Path, default=Path.cwd())
        if verb != "names":
            p.add_argument("--base", required=True)
        if verb == "run":
            p.add_argument("--full", required=True)
            p.add_argument("--structural", default=STRUCTURAL)
            p.add_argument("--runner", default=RUNNER)
    a = ap.parse_args(argv)
    root = a.root.resolve()
    if a.verb == "names":
        print("\n".join(names(root)))
        return 0
    if a.verb == "decide":
        print(decide(root, a.base))
        return 0
    return run(root, a.base, a.full, a.structural, a.runner)


if __name__ == "__main__":
    sys.exit(main())
