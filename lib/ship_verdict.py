"""`goh ship verdict --sha SHA [--repo DIR]`: the RECORDED gate verdict of the ship running the
caller (GOH.VERDICTAPI; docs/contracts.md section 29).

A release kit started by `goh ship` may skip re-running the full gate over a commit already gated --
but only on a verdict goh RECORDED, never on its log text (koffee_big read "pre-push: gating <ref>
@ <sha>" and "gates: all green" out of the push log, so a reworded line turned the skip off with no
one told). The record is the one verdict store's `gate` path (lib/reuse.py, GOH.ONEVERDICT): the
push gate records every commit it gated green, keyed by the repo, the commit's TREE and the gate
identity, on every push -- not only a push `goh ship` runs. The per-ship `green <sha>` file this
read before (GOH_GATE_VERDICTS) missed the commit that matters most: one the land queue had
already pushed, which ship's own push then had "nothing to gate" (koffee_big ship
20261115T121828Z-952ef800, a 3137.5 s release step of two full gates).

The answer is yes (exit 0, the ship record's path printed) only when a ship record of REPO is
`running`, its worker pid is an ANCESTOR of the caller (a stale or hand-written record has no live
worker above the caller), and the store holds a green full-gate verdict on SHA's tree under the
gates and tools of today (lib/reuse.py lookup_gate: a land's record counts too). Anything else is
no (exit 1, the miss's reason on stderr); a bad invocation is 2. Pinned by
tests/test_ship_verdict.py.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # a missing goh.db: the ships are unknown (goh_db.Unknown)
import reuse  # lib/, beside this file: the one verdict store (GOH.ONEVERDICT)
import ship_records  # the ship records in goh.db (GOH.STATEDB S2)


def ancestors(pid: int) -> set[int]:
    """PID and every pid above it, read from `ps` (macOS and Linux alike)."""
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        argv = ["ps", "-o", "ppid=", "-p", str(pid)]
        out = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             check=False).stdout.strip()  # fmt: skip
        if not out.isdigit():
            break
        pid = int(out)
    return seen


def verdict(repo: Path, sha: str, caller: int | None = None) -> tuple[Path | None, str]:
    """(the running ship's record, "") when SHA is green under the ship above CALLER, else
    (None, why not)."""
    repo = repo.resolve()
    live = ancestors(os.getpid() if caller is None else caller)
    why = f"no running ship of {repo.name} is above this process"
    try:
        recs = ship_records.in_dir(ship_records.ship_dir() / repo.name)
    except goh_db.Unknown as e:
        return None, f"the ships are unknown: {e}"
    for path, rec in recs:
        if Path(str(rec.get("repo", ""))).resolve() != repo:
            continue
        if rec.get("status") != "running" or rec.get("pid") not in live:
            continue
        found, why = reuse.lookup_gate(repo, sha)
        if found is not None:
            return path, ""
    return None, why


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh ship verdict", description=__doc__.splitlines()[0])
    ap.add_argument("--sha", required=True, help="the full commit sha the gate judged")
    ap.add_argument("--repo", default=".", help="the checkout (default: the current one)")
    args = ap.parse_args(argv)
    top = subprocess.run(["git", "-C", args.repo, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         check=False).stdout.strip()  # fmt: skip
    found, why = verdict(Path(top or args.repo), args.sha)
    if found is None:
        print(f"goh ship verdict: no: {why}", file=sys.stderr)
        return 1
    print(found)
    return 0
