"""The fresh full instrumented run takes this tree's own passes of tests that run no binary.

GOH.SHIPBREACH0137. A ship stands on a fresh full INSTRUMENTED run of main's tip (the owner's term
2, lib/ship_suite.py). A land that reused the Rust coverage measurement (gates/_rust_proven.sh, the
binary's build inputs unchanged) ran its suite against the PLAIN binary, so its tree had no such
record and the ship ran all ~6400 tests again under the instrumented binary: 658-1662 s a ship
(v0.139.0, v0.139.1, v0.139.3: 3311-4602 cpu-s each), of which the tests that never run the binary
were about 73% of the test time (3613 of 4965 serial s, tree 829e765a) and add no coverage.

Such a test's verdict is the same under either binary: it runs neither. So the ship's run
(GOH_LAND_VERDICTS_REUSE=@tree, lib/ship_full_run.py) skips a node only when all three hold:
  * a run JUDGED it on this very tree, under these tools (the record's `judged`: a pass this tree's
    pre-step or gate made, never one carried to it from another tree, lib/reuse.py record_suite);
  * that pass ran no binary (the record's `passed` value);
  * an instrumented run MEASURED it running none (the record's `measured` is False: COVNONBIN's
    claim, checked by every instrumented run that meets it, tests/_cov_claims.py).
Every test that runs the binary, every carried one and every unmeasured one runs, instrumented, on
this tree: the coverage profile is the whole of what this tree's binary-running tests cover (a
skipped node leaves none), so the run is still the fresh full instrumented one, and the shadow the
coverage carry is compared with (GOH_PROFCARRY_AUDIT=fresh) sees exactly what a run of all would.
Nothing carries from another tree, no stored profile is taken, and each skip is one use of the
audited `test` path, as a land's same-tree reuse is (tests/_land_audit.py).
"""

from __future__ import annotations

SAME = "@tree"  # GOH_LAND_VERDICTS_REUSE's value for this mode; no file is named so


def nodes(rec: dict | None) -> set[str]:
    """The nodes of this tree's suite record the fresh instrumented run may skip (see above)."""
    if not rec or not isinstance(rec.get("passed"), dict):
        return set()
    passed, measured = rec["passed"], rec.get("measured")
    measured = measured if isinstance(measured, dict) else {}
    judged = rec.get("judged") if isinstance(rec.get("judged"), list) else []
    return {n for n in judged if passed.get(n) is False and measured.get(n) is False}


def plan(rec: dict | None, coverage: bool, audited) -> dict:
    """The run's plan (tests/_land_verdicts.py `_plan`): `audited(plan, nodes)` is the `test`
    path's decision, the nodes it lets skip (a sampled one joins the plan's shadows and runs)."""
    out: dict = {"lines": [], "skipped": {}, "other": 0, "shadows": []}
    found = nodes(rec) if coverage else set()
    if not coverage:
        out["lines"].append("land verdicts: nothing reused: @tree serves an instrumented run alone")
    elif not found:
        out["lines"].append("land verdicts: no pass judged on this tree measured running no binary")
    else:
        out["lines"].append(
            f"land verdicts: {len(found)} judged on this tree, measured running no "
            "binary, are reused (@tree: every other test runs instrumented)"
        )
    same = audited(out, found) if found else set()  # a sampled node runs: the `test` shadow
    out.update(skipped=dict.fromkeys(same, False), skip=set(same), judged=set(same),
               nobin=set(same))  # fmt: skip
    return out
