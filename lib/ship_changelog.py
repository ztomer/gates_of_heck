"""goh ship's CHANGELOG.md reading, under the contract the release kit DECLARES (GOH.SHIPCHANGELOG).

token-pool's ship 20261112T024215Z-281911da ran its whole gate and push, then its kit refused
("CHANGELOG.md has no '## Unreleased' section to release"): the reading assumed changelog_cut's
contract, under which a file with no Unreleased heading passed. `.gatesrc` GOH_SHIP_CHANGELOG names
the contract instead, never guessed:

* `cut`: goh's changelog_cut (an Unreleased entry is cut into a stanza, or one is written by hand);
* `unreleased`: a kit that renames its ONE non-empty `## Unreleased` heading (or tags a written
  `## vX` stanza);
* `kit`: the kit cuts its own changelog, or keeps none; its GOH_SHIP_PREFLIGHT asks what it needs.

A repo with a CHANGELOG.md that declares none, or an unknown one, is refused with nothing started.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

CONTRACTS = ("cut", "unreleased", "kit")
CUT = Path(__file__).resolve().parent.parent / "tools" / "release-kit" / "changelog_cut.py"


def reading(repo: Path, declared: str, version: str, summary: str) -> list[tuple[str, str]]:
    """The preflights [(whose, command)] that ask CHANGELOG.md what the kit will, [] when the
    contract asks nothing; ValueError naming the fault when the contract is missing or unknown."""
    contract = declared.strip()
    has_log = (repo / "CHANGELOG.md").is_file()
    if contract and contract not in CONTRACTS:
        raise ValueError(f"{repo.name}: GOH_SHIP_CHANGELOG={contract!r} is none of "
                         f"{', '.join(CONTRACTS)}")  # fmt: skip
    if has_log and not contract:
        raise ValueError(f"{repo.name}: CHANGELOG.md exists and .gatesrc declares no "
                         "GOH_SHIP_CHANGELOG, the contract its release kit holds, so what the kit "
                         "will refuse cannot be asked before the push: declare cut (goh's "
                         "changelog_cut), unreleased (the kit renames its one ## Unreleased) or "
                         "kit (the kit cuts its own)")  # fmt: skip
    if contract not in ("cut", "unreleased"):
        return []
    if not has_log:
        raise ValueError(f"{repo.name}: GOH_SHIP_CHANGELOG={contract} and there is no CHANGELOG.md "
                         "for the kit to release")  # fmt: skip
    ready = (f"{shlex.quote(sys.executable)} {shlex.quote(str(CUT))} ready --version {version} "
             f"--file CHANGELOG.md --contract {contract} "
             f"--title {shlex.quote(summary)}")  # fmt: skip
    return [(f"goh's changelog reading, contract {contract}", ready)]
