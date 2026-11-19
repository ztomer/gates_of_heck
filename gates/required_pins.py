"""required_pins.py -- does THIS interpreter hold the manifest's pip pins? (GOH.PYTESTPIN)

`gates/required_tools.py --layer L --pins-held` asks it. Hosted CI installs every pinned pip row
at exactly its pin (`required_tools.py --install` writes `tool==X.Y.Z`); a box on another release
runs another gate. v0.139.6's release CI was red on a test id pytest 9.2.0 changed while the box
ran 9.1.1, and the local gate had passed the tree. tools/pytest.sh refuses to run on a box that
fails this, so goh's local suite is CI's.
"""

from __future__ import annotations

import sys
from importlib import metadata


def held(selected: list[dict]) -> int:
    """Red (1), naming both releases, for each pinned pip row of `selected` whose distribution in
    this interpreter is missing or is not exactly its pin; else 0."""
    failures = []
    for row in {r["tool"]: r for r in selected if r["kind"] == "pip" and r["pin"]}.values():
        try:
            found = metadata.version(row["tool"])
        except metadata.PackageNotFoundError:
            found = ""
        if found != row["pin"]:
            failures.append(
                f"{row['tool']} is {found or 'not installed'} in {sys.executable} and the manifest "
                f"pins {row['pin']}, the release hosted CI installs -- {row['install']}"
            )
    for reason in failures:
        print(f"required_tools: {reason}", file=sys.stderr)
    return 1 if failures else 0
