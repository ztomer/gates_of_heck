"""Which tests the coverage step may carry as running no binary, and the gate on that claim.

GOH.COVNONBIN (D-0011). Under coverage a test is carried as running no binary only when an
instrumented run MEASURED it running none (tests/_prof_record.py, GOH.COVCARRY). That fact lived in
the record of the tree the instrumented run judged alone: a plain gate kept it for the nodes it
SKIPPED (a shadow, which runs a source's carried nodes, dropped them all) and a reused pre-step
verdict (lib/pre_reuse.py, GOH.SUITEONCE) recorded none, so the next instrumented land carried
nothing and ran all 4668 such tests again (GOH.COVSUITECOST: 2262 of 3156 cpu-s, tree ec82d57c).
Now it carries as a verdict does: a node measured running none on a source the diff does not
choose (tests/_land_verdicts.py `nobin`) ran none on this tree, whether this run skipped or ran it.

THE GATE. Every instrumented run -- a land's coverage step, the idle full run on main's tip --
checks the claims it meets (the sources' it carried, and the store's for its own tree) against
what it measured: a claimed node that left a profile turns the run red, naming it, and the tree's
record then holds what the node did. Pinned by tests/test_cov_nonbin.py.
"""

from __future__ import annotations

import pytest


def misplaced(claimed: dict[str, bool], ran_bin: set[str]) -> list[str]:
    """The nodes of `ran_bin` (each left a profile in this instrumented run) that `claimed` says
    ran no binary: a carried claim the measurement refutes."""
    return sorted(n for n in ran_bin if claimed.get(n) is False)


def record(session, plan: dict, rec: dict | None, passed: dict[str, bool],
           measured: dict[str, bool], ran_bin: set[str], instrumented: bool) -> None:  # fmt: skip
    """Add to `measured` each node of the plan's `nobin` this run passed without running the
    binary; under `instrumented`, turn the session red for each claim (`nobin`, and `rec`, this
    tree's store record) that a node which ran the binary refutes."""
    nobin = plan.get("nobin", set())
    for n in nobin:
        if n in passed and n not in ran_bin:
            measured.setdefault(n, False)
    if not instrumented:
        return
    claimed = dict.fromkeys(nobin, False)
    old = rec.get("measured") if rec else None
    if isinstance(old, dict):
        claimed.update((n, False) for n, b in old.items() if b is False)
    bad = misplaced(claimed, ran_bin)
    if bad:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    term = session.config.pluginmanager.get_plugin("terminalreporter")
    for n in bad:
        (term.write_line if term else print)(
            f"land verdicts: {n} ran the instrumented binary but was carried as running none "
            "(a measurement the diff did not reach: tools/affected_tests.py misses an input of "
            "it) -- the coverage it adds was never counted on the trees that carried it"
        )
