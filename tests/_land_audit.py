"""The land verdicts' audited reuses (tests/_land_verdicts.py; lib/reuse_audit.py, GOH.REUSEAUDIT).

Two paths of lib/reuse.py skip tests in a land's gate: `carry` (a source tree's passes carried
across a diff that does not choose them; one use per source) and `test` (a node the pre-land run
passed on this very tree; one use per node, GOH.REUSEAUDIT2). The controller decides every use once
and hands its workers the node list, so every xdist worker deselects alike; a sampled use's nodes
run anyway, and at session end each is compared with the green it would have reused. A failure is
re-run in a child pytest, and the record read again, before the audit hears it: only a repeated
disagreement turns the path off. Pinned by tests/test_verdict_carry.py and
tests/test_land_verdicts_audit.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

TEST, CARRY = "test", "carry"
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib")


def _live(node: str) -> bool:
    sys.path.insert(0, _LIB)
    try:
        import live_reads  # lib/: the modules whose input no reuse key holds (GOH.REUSEOFFTEST)
    finally:
        sys.path.pop(0)
    return live_reads.live(node)


def audited(plan: dict, same: set[str], ident: str, rootdir: str, in_store) -> set[str]:
    """The same-tree nodes this run may skip. A sampled node joins the shadows and runs; a path
    the audit turned off, or no store to count the uses in (`in_store` None), reuses none."""
    if in_store is None:
        plan["lines"].append("land verdicts: none reused after all: no verdict store to audit in")
        return set()
    if unkeyed := {n for n in same if _live(n)}:  # no key holds what they read: they run
        plan["lines"].append(f"land verdicts: {len(unkeyed)} of them RUN: they read the live loop")
        same = same - unkeyed
    nodes = sorted(same)
    with in_store() as (_, audit):
        decided = audit.use_many("test", [{"identity": ident, "node": n} for n in nodes])
    if "off" in decided:
        plan["lines"].append("land verdicts: none reused after all: the audit turned `test` off "
                             "(lib/reuse_audit.py report)")  # fmt: skip
        return set()
    shadow = [n for n, d in zip(nodes, decided, strict=True) if d == "shadow"]
    if shadow:
        plan["shadows"].append({"path": TEST, "identity": ident, "nodes": shadow,
                                "rootdir": rootdir})  # fmt: skip
        plan["lines"].append(f"land verdicts: {len(shadow)} of them RUN anyway: the audit's shadow")
    return same - set(shadow)


def rerun(rootdir: str, bad: list[str], drop: tuple[str, ...]) -> str:
    """The nodes run again in a child pytest that reuses nothing: green or red."""
    env = {k: v for k, v in os.environ.items() if k not in drop}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *bad],
                       cwd=rootdir, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return "green" if r.returncode == 0 else "red"


def shadows(plan: dict, in_store, called: set[str], failed: set[str], drop: tuple[str, ...],
            still_passed: Callable[[str], bool]) -> None:  # fmt: skip
    """Compare each shadow's nodes that ran with the green reused for them. `still_passed(node)`
    reads the pre-land record again; a carry source's record is looked up again in the store."""
    if not plan.get("shadows"):
        return
    with in_store() as (reuse, audit):
        for sh in plan["shadows"]:
            ran = set(sh["nodes"]) & (called | failed)
            if not ran:
                continue  # none ran (an interrupted run): nothing to compare
            if sh.get("path") == TEST:  # each node its own use, so its own comparison
                for n in sorted(ran):
                    audit.compare(TEST, {"identity": sh["identity"], "node": n}, "green",
                                  "red" if n in failed else "green",
                                  lambda n=n, sh=sh: ("green" if still_passed(n) else "",
                                                      rerun(sh["rootdir"], [n], drop)))  # fmt: skip
                continue
            bad = sorted(ran & failed)

            def again(sh=sh, bad=bad) -> tuple[str, str]:
                rec, _ = reuse.lookup(CARRY, sh["fields"])
                kept = rec and all(n in rec.get("passed", {}) for n in bad)
                return ("green" if kept else ""), rerun(sh["rootdir"], bad, drop)

            audit.compare(CARRY, sh["fields"], "green", "red" if bad else "green", again)
