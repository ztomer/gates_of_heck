#!/usr/bin/env python3
"""A TRACKED file that is build output: the shape a build leaves behind, committed by accident.

koffee_oss asked for this on 2026-10-19 (BACKLOG.md, "no tracked build output"): six files under
`DerivedDataTests/` came in with the history flatten, and the `.gitignore` check cannot see them --
it cannot see a file that is ALREADY tracked, which is the only case that matters.

    check_tracked_build_output.py [--root DIR]

Exit 0: no tracked file looks like build output.
Exit 1: findings, each naming the file and the shape it matched.
Exit 2: usage/config error (or: this is not a git repository).

THE SHAPES. A directory a build writes into: `target/` (Rust, Maven, Gradle), `DerivedData/`
(Xcode), `__pycache__/` (Python), `node_modules/` (npm), `.build/` (SwiftPM), `.venv/`. A compiled
or bundled file: `.o .obj .a .so .dylib .dll .pyc .pyo .class .xcactivitylog`, and the `.dSYM`,
`.app` and `.ipa` bundles.

WHAT IT IS NOT. It reads the TRACKED files (`git ls-files`), so it says nothing about a build
directory that is merely ON DISK: that is what `.gitignore` is for, and `goh`'s tracked-ignored
step already covers a file that is tracked AND ignored. And a repository that tracks nothing is
REFUSED, not passed: a scan that found no subject has not judged anything.
"""

from __future__ import annotations

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
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env, repo_root

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, ok

# A directory a build writes into.
DIR_SEGMENTS = (
    "target",
    "DerivedData",
    "__pycache__",
    "node_modules",
    ".build",
    ".venv",
)

# A compiled file.
FILE_SUFFIXES = (
    ".o",
    ".obj",
    ".a",
    ".so",
    ".dylib",
    ".dll",
    ".pyc",
    ".pyo",
    ".class",
    ".xcactivitylog",
)

# A bundle a build produced.
BUNDLE_SUFFIXES = (".dSYM", ".app", ".ipa")


def tracked(root):
    """The repository's tracked files, or None when git could not answer."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=foreign_repo_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.split("\0") if p]


def findings(paths):
    """(path, why) for every tracked file that looks like build output."""
    out = []
    for path in paths:
        parts = path.split("/")
        segment = next((s for s in parts[:-1] if s in DIR_SEGMENTS), None)
        if segment is not None:
            out.append((path, f"inside a build directory ({segment}/)"))
            continue
        bundle = next((s for s in parts if s.endswith(BUNDLE_SUFFIXES)), None)
        if bundle is not None:
            out.append((path, f"a build bundle ({bundle})"))
            continue
        if path.endswith(FILE_SUFFIXES):
            out.append((path, "a compiled file"))
            continue
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=None, help="the repository to judge (default: this one)")
    args = parser.parse_args(argv)
    root = args.root or repo_root()
    paths = tracked(root)
    if paths is None:
        err("tracked build output: git could not list the tracked files -- nothing judged")
        return 2
    if not paths:
        err("tracked build output: this repository tracks no files -- nothing to judge")
        return 1
    found = findings(paths)
    if found:
        err(f"tracked build output: {len(found)} tracked file(s) look like build output:")
        for path, why in found:
            err(f"  {path} -- {why}")
        err("  a build's output is not source: take it out of the tree, and let .gitignore keep it")
        return 1
    ok(f"tracked build output: none of the {len(paths)} tracked file(s) looks like build output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
