"""goh ship's CHANGELOG.md reading, under the contract the release kit DECLARES (GOH.SHIPCHANGELOG).

token-pool's ship 20261112T024215Z-281911da ran its whole gate and push, then its kit refused
("CHANGELOG.md has no '## Unreleased' section to release"): the reading assumed changelog_cut's
contract, under which a file with no Unreleased heading passed. `.gatesrc` GOH_SHIP_CHANGELOG names
the contract instead, never guessed:

* `cut`: goh's changelog_cut (an Unreleased entry is cut into a stanza, or one is written by hand);
* `unreleased`: a kit that renames its ONE non-empty `## Unreleased` heading (or tags a written
  `## vX` stanza);
* `kit`: the kit cuts its own changelog, or keeps none; its GOH_SHIP_PREFLIGHT asks what it needs.

A repo with a changelog that declares none, or an unknown one, is refused with nothing started.

The changelog is the first of the house's LOCATIONS the repo has -- `CHANGELOG.md`, else
`docs/CHANGELOG.md`, the ones `goh changelog-tags` reads (`crates/goh/src/changelog_tags.rs`,
pinned equal by tests/test_ship_changelog.py). Only the root was read, so a docs/ changelog (two
consumers) was never asked before the push and `cut` was refused there (GOH.SHIPCLDOCS).

A repo whose `.gatesrc` says GOH_CHANGELOG_FRAGMENTS=on keeps each change's entry in its own file
under `changelog/` beside the changelog (GOH.CLASS.CHECKOUT): the reading folds them in as the cut
will, so a release whose Unreleased is empty and whose fragments hold its entries is ready.

goh ship's CHANGELOG.md reading under the contract the release kit DECLARES (`.gatesrc`
GOH_SHIP_CHANGELOG: `cut`, `unreleased` or `kit`, GOH.SHIPCHANGELOG): the preflight it asks before
any gate or push, and a refusal when a repo with a CHANGELOG.md declares none or an unknown one.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

CONTRACTS = ("cut", "unreleased", "kit")
CUT = Path(__file__).resolve().parent.parent / "tools" / "release-kit" / "changelog_cut.py"
LOCATIONS = ("CHANGELOG.md", "docs/CHANGELOG.md")


def changelog_of(repo: Path) -> str | None:
    """The repo's changelog, repo-relative: the first of LOCATIONS it has, else None."""
    return next((c for c in LOCATIONS if (repo / c).is_file()), None)


def reading(repo: Path, declared: str, version: str, summary: str,
            fragments: str = "") -> list[tuple[str, str]]:  # fmt: skip
    """The preflights [(whose, command)] that ask CHANGELOG.md what the kit will, [] when the
    contract asks nothing; ValueError naming the fault when the contract is missing or unknown,
    or GOH_CHANGELOG_FRAGMENTS (`fragments`) is neither on nor off."""
    contract = declared.strip()
    if fragments.strip() not in ("", "on", "off"):
        raise ValueError(
            f"{repo.name}: GOH_CHANGELOG_FRAGMENTS={fragments!r} is neither on nor off"
        )
    log = changelog_of(repo)
    has_log = log is not None
    if contract and contract not in CONTRACTS:
        raise ValueError(f"{repo.name}: GOH_SHIP_CHANGELOG={contract!r} is none of "
                         f"{', '.join(CONTRACTS)}")  # fmt: skip
    if has_log and not contract:
        raise ValueError(f"{repo.name}: {log} exists and .gatesrc declares no "
                         "GOH_SHIP_CHANGELOG, the contract its release kit holds, so what the kit "
                         "will refuse cannot be asked before the push: declare cut (goh's "
                         "changelog_cut), unreleased (the kit renames its one ## Unreleased) or "
                         "kit (the kit cuts its own)")  # fmt: skip
    if contract not in ("cut", "unreleased"):
        return []
    if not has_log:
        raise ValueError(f"{repo.name}: GOH_SHIP_CHANGELOG={contract} and there is no changelog "
                         f"({' or '.join(LOCATIONS)}) for the kit to release")  # fmt: skip
    ready = (f"{shlex.quote(sys.executable)} {shlex.quote(str(CUT))} ready --version {version} "
             f"--file {shlex.quote(log)} --contract {contract} "
             f"--title {shlex.quote(summary)}")  # fmt: skip
    if fragments.strip() == "on":
        ready += f" --fragments {shlex.quote(str(Path(log).parent / 'changelog'))}"
    return [(f"goh's changelog reading, contract {contract}", ready)]
