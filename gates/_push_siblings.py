"""_push_siblings.py -- the sibling checkouts a push export's tracked manifests name.

A path dependency that leaves the repository (`goh-tui = { path = "../gates_of_heck/..." }` in a
Cargo.toml, `.package(path: "../../ZoneWM/Packages/SpaceBarKit")` in a nested Package.swift)
resolves BESIDE the checkout, and beside a push export there is nothing but its tree. The push gate
links each sibling named here into the run directory (lib/push_runs.sh, `export_siblings`).

The manifests are the TRACKED ones (`git ls-files`, no walk), so an untracked scratch manifest
never decides what the export holds. A path that climbs past the checkout's parent names no
sibling and is left to the build to refuse in its own words.

usage: _push_siblings.py <export tree>     prints one sibling name per line
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

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks"))
from _gitutil import foreign_repo_env

PATTERNS = {
    "Cargo.toml": re.compile(r'\bpath\s*=\s*"([^"]+)"'),
    "Package.swift": re.compile(r'\.package\(\s*(?:name:\s*"[^"]*"\s*,\s*)?path:\s*"([^"]+)"'),
}


def siblings(tree: str) -> list[str]:
    listed = subprocess.run(
        ["git", "-C", tree, "ls-files", "-z", "--", *(f"**/{n}" for n in PATTERNS), *PATTERNS],
        env=foreign_repo_env(),  # the export is not the repo the hook runs for
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    names: list[str] = []
    for rel in sorted(set(filter(None, listed.split("\0")))):
        pattern = PATTERNS.get(os.path.basename(rel))
        if pattern is None:
            continue
        try:
            with open(os.path.join(tree, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for found in pattern.findall(text):
            parts = os.path.normpath(os.path.join(os.path.dirname(rel), found)).split(os.sep)
            outside = len(parts) >= 2 and parts[0] == ".." and parts[1] not in ("", "..", ".")
            if outside and parts[1] not in names:
                names.append(parts[1])
    return names


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    for name in siblings(argv[1]):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
