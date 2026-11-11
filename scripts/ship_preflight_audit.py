#!/usr/bin/env python3
"""The estate's release kits that declare no ship preflight: a ratchet (GOH.SHIPPREFLIGHT).

    ship_preflight_audit.py [--root DIR]          # check: exit 1 on an unseeded or a stale entry
    ship_preflight_audit.py [--root DIR] --seed   # write today's list as the seed

`goh ship` pushes first and runs the repo's release kit after, so a precondition the kit checks
that was knowable before the push is found with the hand-off already public (token-pool:
"CHANGELOG.md has no '## Unreleased' section"; a private consumer: "its plan's done ledger has no
row for v5.18.0"). A repo closes that by declaring `.gatesrc` GOH_SHIP_PREFLIGHT, which ship runs in
plan(), before its first step. This lists every estate repo that declares GOH_SHIP_RELEASE and no
GOH_SHIP_PREFLIGHT (read the way ship reads them, `tools/ship.py config`).

THE RATCHET. Today's list is the seed; "done" is an empty seed. A finding the seed does not name is
red (a new kit must declare its preflight); a seeded repo that now declares one, no longer ships a
release, or is gone is STALE and red (delete its entry with the fix). A seeded finding is a warning.

The seed lives in the owner's PRIVATE store, never in this public tree (GOH.SEEDPRIVATE): the file
`ship_preflight.json` under `$GOH_SEED_DIR`, else `$XDG_STATE_HOME/goh/seeds`, else
`~/.local/state/goh/seeds`, `{"repos": {"<repo relative to the root>": "<what it checks late>"}}`.
The estate is the `~/Projects` walk unioned with the install registry (`lib/estate_registry.py`).
Exit 0 clean or only seeded findings, 1 an unseeded or stale entry, 2 no seed file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lib"), str(ROOT / "tools")]
SEED_NAME = "ship_preflight.json"


def seed_path() -> Path:
    if os.environ.get("GOH_SEED_DIR"):
        return Path(os.environ["GOH_SEED_DIR"]).expanduser() / SEED_NAME
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "goh" / "seeds" / SEED_NAME


def label(repo: Path, root: Path) -> str:
    try:
        return str(repo.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(repo)


def findings(root: Path) -> list[str]:
    """Every estate repo whose .gatesrc declares GOH_SHIP_RELEASE and no GOH_SHIP_PREFLIGHT."""
    import estate_registry
    import ship  # ship's own reading of the keys, never a copy

    repos, _ = estate_registry.union(estate_registry.walk_repos(root))
    env_keys = [k for k in ship.KEYS if k in os.environ]
    for k in env_keys:  # the environment beats .gatesrc in ship.config: read each repo's own
        os.environ.pop(k)
    out = []
    for repo in repos:
        if not (repo / ".gatesrc").is_file():
            continue
        cfg = ship.config(repo)
        if cfg["GOH_SHIP_RELEASE"].strip() and not cfg["GOH_SHIP_PREFLIGHT"].strip():
            out.append(label(repo, root))
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", default=str(Path.home() / "Projects"))
    ap.add_argument("--seed", action="store_true", help="write today's findings as the seed")
    a = ap.parse_args(argv)
    from tui.lib import err, ok, warn

    root = Path(a.root).expanduser()
    found = findings(root)
    path = seed_path()
    if a.seed:
        old = (
            json.loads(path.read_text(encoding="utf-8")).get("repos", {}) if path.is_file() else {}
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        repos = {r: old.get(r, "unreviewed") for r in found}
        path.write_text(json.dumps({"repos": repos}, indent=1) + "\n", encoding="utf-8")
        ok(f"{path}: {len(repos)} seeded")
        return 0
    if not path.is_file():
        err(f"no seed at {path}: run {Path(__file__).name} --seed once")
        return 2
    seeded = json.loads(path.read_text(encoding="utf-8")).get("repos", {})
    bad = 0
    for r in found:
        if r in seeded:
            warn(f"{r}: its release kit declares no GOH_SHIP_PREFLIGHT (seeded: {seeded[r]})")
        else:
            err(f"{r}: declares GOH_SHIP_RELEASE and no GOH_SHIP_PREFLIGHT, and is not "
                "seeded: declare the kit's pre-push checks as GOH_SHIP_PREFLIGHT")  # fmt: skip
            bad += 1
    for r in sorted(set(seeded) - set(found)):
        err(f"{r}: seeded in {path} but no longer a finding (a preflight declared, no release, "
            "or gone): delete its entry")  # fmt: skip
        bad += 1
    if not bad:
        ok(f"ship preflight: seeded kits without one: {len(found)}; nothing new or stale")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
