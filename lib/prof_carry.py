"""The coverage carry's audit (GOH.COVCARRY2, the owner's term 3 for D-0011's profile carry).

    prof_carry.py claim --repo R --summary FILE [--by TEXT]   # a land's coverage claim on R's tree
    prof_carry.py ask --repo R                               # may coverage reuse: reuse|shadow|off
    prof_carry.py compare --repo R --summary FILE            # a FRESH full instrumented run of R's
                                                             # tree against the land's claim

WHY. A land's coverage number stands on reuse: the suite's nodes carried from another tree (only
those an instrumented run measured running no binary, GOH.COVCARRY term 1) and, on a tree whose Rust
inputs are unchanged, rustcov's reused measurement (the tests run plain, GOH.RUSTCOVSKIP), which was
never sampled: its shadow is a whole instrumented build and suite. The idle lander makes exactly
that run on main's tip -- the tree the last land judged -- and a ship requires one (term 2,
lib/full_run.py INSTRUMENTED). So that run is the shadow: it compares its own per-crate summary
with the one the land claimed for the same tree.

THE CLAIM is the per-crate summary gates/lcov_merge.py writes (`--summary`: covered and coverable
lines per `crates/<name>/`, the floor's own count), recorded under the `profcarry` path keyed on
(repo, tree, tools): by the coverage step after a green merge (gates/_coverage_rust.sh), or by a
rustcov reuse, from the summary kept beside the measurement it reuses (gates/_rust_proven.sh).

THE COMPARE (`reuse_audit.compare("profcarry", ...)`). The coverable lines of every crate must be
EQUAL (another count is another binary), and the covered lines within NOISE_LINES. The band was a
guess (3 lines, or 1 in 2000: 13 of goh's 27517), on the theory that timing-dependent paths (a
retry, a deadline) move a few lines from one full run to the next. MEASURED (GOH.COVCARRY3,
2026-11-18), two full instrumented runs of one set of Rust inputs -- a land's claim on 5bce690b's
tree and a lane's run of 6d066d8c's, at load ~48 -- covered the SAME lines in every crate (goh
26844/27517, goh-golden 392/397, goh-sys 158/163, goh-testkit 119/121, goh-tui 80/106): the band is
0, and a measured disagreement re-opens it (tests/test_prof_carry.py). A claim outside it
is a carried node whose run was coverage after all, or a reused measurement of other inputs:
the path goes off, a fault is filed, and every reuse it gates stops -- `on()` is false, so the
coverage run carries nothing (tests/_land_verdicts.py) and rustcov measures. The fresh side is a
whole instrumented suite, so its re-run for the audit's "again" is the store and the report read
again: a disagreement repeats, and one is enough.

A missing claim (a tree no land judged, a claim past its TTL) compares nothing and says so.
Pinned by tests/test_prof_carry.py.
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
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_identity  # lib/, beside this file
import reuse
import reuse_audit

PATH = "profcarry"
NOISE_LINES = 0  # covered lines a crate may move between two full runs: measured 0 (above)


def fields(repo: Path) -> dict[str, str] | None:
    """The claim's key on `repo`'s clean tree; None for a dirty one (it has no tree to claim)."""
    tree = gate_identity.clean_tree(repo)
    if tree is None:
        return None
    return {"repo": reuse.repo_id(repo), "tree": tree, "tools": gate_identity.suite_tools()}


def load(summary: Path) -> dict[str, list[int]]:
    crates = json.loads(Path(summary).read_text(encoding="utf-8")).get("crates")
    if not isinstance(crates, dict):
        raise TypeError(f"{summary}: no `crates` table")
    return {str(k): [int(v[0]), int(v[1])] for k, v in crates.items()}


def canon(crates: dict[str, list[int]]) -> str:
    return "; ".join(f"{k} {c}/{t}" for k, (c, t) in sorted(crates.items()))


def parse(text: str) -> dict[str, list[int]]:
    out = {}
    for part in filter(None, (p.strip() for p in text.split(";"))):
        name, _, frac = part.rpartition(" ")
        c, _, t = frac.partition("/")
        out[name] = [int(c), int(t)]
    return out


def agree(claimed: dict[str, list[int]], fresh: dict[str, list[int]]) -> bool:
    """The same crates, the same coverable lines each, the covered within the noise band."""
    if claimed.keys() != fresh.keys():
        return False
    for name, (cov, tot) in claimed.items():
        f_cov, f_tot = fresh[name]
        if tot != f_tot or abs(cov - f_cov) > NOISE_LINES:
            return False
    return True


def claim(repo: Path, summary: Path, by: str) -> str:
    f = fields(repo)
    if f is None:
        return "coverage claim: none recorded (the tree is not clean)"
    reuse.record(PATH, f, canon(load(summary)), by=by)
    reuse.prune(PATH)
    return f"coverage claim: recorded for tree {f['tree'][:12]} ({by})"


def ask(repo: Path) -> str:
    """One audited use before a coverage reuse: "reuse", "shadow" (reused; the idle run is the
    shadow) or "off"."""
    f = fields(repo)
    return "off" if f is None else reuse_audit.use("profcarry", f)


def on() -> bool:
    """The carry's kill switch: False once a fresh run disagreed with a claim (or starved)."""
    return reuse_audit.state(PATH)["state"] == "on"


def compare(repo: Path, summary: Path) -> str:
    f = fields(repo)
    if f is None:
        return "coverage audit: nothing compared (the tree is not clean)"
    rec, why = reuse.lookup(PATH, f)
    if rec is None:
        return f"coverage audit: no claim on tree {f['tree'][:12]} to compare ({why})"
    reused = str(rec["verdict"])

    def fresh_side() -> str:
        got = load(summary)
        return reused if agree(parse(reused), got) else canon(got)

    def again() -> tuple[str, str]:
        rec2, _ = reuse.lookup(PATH, f)
        return (str(rec2["verdict"]) if rec2 else ""), fresh_side()

    fresh = fresh_side()
    same = reuse_audit.compare(PATH, f, reused, fresh, again)
    if same:
        return (
            f"coverage audit: the fresh run agrees with {rec.get('by') or 'the'} claim ({reused})"
        )
    return f"coverage audit: DISAGREE -- claimed {reused}, measured {fresh}; profcarry is off"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("verb", choices=("claim", "ask", "compare"))
    ap.add_argument("--repo", required=True)
    ap.add_argument("--summary", default="")
    ap.add_argument("--by", default="coverage step")
    a = ap.parse_args(argv)
    repo = Path(a.repo)
    try:
        if a.verb == "ask":
            print(ask(repo))
        elif a.verb == "claim":
            print(claim(repo, Path(a.summary), a.by))
        else:
            print(compare(repo, Path(a.summary)))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as e:  # the audit never fails the gate
        said = f"coverage audit: {a.verb} not done ({e.__class__.__name__}: {e})"
        print("off" if a.verb == "ask" else said)  # `ask` answers one word, which a shell reads
    return 0


if __name__ == "__main__":
    sys.exit(main())
