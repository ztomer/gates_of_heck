"""lib/long_pole.py -- the longest test every enqueue runs (GOH.PRELONGPOLE).

    long_pole.py [--root DIR] [--limit S] [--check]    # the longest test, the sum, the offenders

THE STANDING SET is what `goh land --enqueue`'s pre-step chooses for an EMPTY diff
(`tools/affected_tests.py` `affected(root, [], enqueue=True)`): the judges whose reads are not
known, which every land pays whatever it changes. Its longest single test is the floor under every
land's pre-step, whatever the worker count: xdist cannot split one test (tests/test_self_walk.py's
two whole-tree walks were 147 s, tests/test_land_check.py's replay 98 s, on 2026-11-21), so the
10-minute land plan needs each of them short, or its reads declared so a diff that cannot move
them does not run them (lib/declared_reads.py).

THE TABLE is the suite's long-pole table (`fast_tests.load("pole")`, goh.db's readings of kind
`pole` beside the duration table's, GOH.DURATIONSDB, written by the same top-level runs), keyed by
node id with its parameters, so a parametrized test's cases are judged one by one: under xdist a
case is a test of its own. Each entry is `pole()` summed over the test's phases: the seconds it
would take on a QUIET box, read from one loaded run (GOH.LONGPOLELOAD). The wall alone read the
machine, not the test: tests/test_self_walk.py's shell case read 168.2 s against its 150 s allowance
at load ~50, and this file's own real-tree test went red the same way, on two lands whose diff
touched neither (2026-11-21). The correction is one stretch sample and cannot fix a wall the load
moved across a whole test, so a reading taken above QUIET_LOAD is UNMEASURED: the entry stays the
last quiet run's, unless the test's CPU outgrew what any load adds (`settle`,
GOH.LONGPOLEUNMEASURED: one loaded run turned every land after it red, 2026-11-23). A cached id the
tree no longer defines is not judged (the table prunes a deleted file, never a deleted function),
and an id with no parameters beside the same function's parametrized ones is the stale one from
before the split.

THE RATCHET is `tests/long_pole_allow.json`: file -> {"limit_s", "why"}, today's tests over RED_S
that could not be cut yet (the ones over LIMIT_S, the target, are `report`'s). It only shrinks
(tests/test_long_pole.py): a test over its file's limit, or over RED_S with no entry, is red; an
entry for a file the standing set no longer holds is stale and red. An entry whose tests all run
fast is NOT stale-red: the table is each test's LAST run, under that run's load (a 16-worker
pre-step triples some tests: 14 s alone, 47 s in it, and the land gate's own run read one file at
8 s), so "cut" cannot be read from one number; `report` names such an entry for a person to delete.
The first two lands of this ratchet (2026-11-21) went red on exactly that: a line at the target
flapped with the load of the last run, and RED_S is the line a reading cannot cross by load alone.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LIMIT_S = 30.0  # the target: what `report` calls long, and what the 10-minute land plan wants
# The RATCHET's red line. The table is each test's LAST run, under that run's load: a 16-worker
# pre-step reads a test at up to three times its time alone (14 s alone, 47 s in it) and the land
# gate's own run read the same file at 8 s, so a hard line AT the target flapped red and green on
# the load of the last run (the first two lands of this ratchet, 2026-11-21). A test read over
# this is over the target by any load.
RED_S = 60.0
ALLOW = ROOT / "tests" / "long_pole_allow.json"


def _fast():
    """lib/fast_tests.py, the duration table's one reader (it imports from the root)."""
    sys.path.insert(0, str(ROOT))
    try:
        import fast_tests
    finally:
        sys.path.remove(str(ROOT))
    return fast_tests


def pole(wall: float, cpu: float, factor: float) -> float:
    """A phase's seconds on a quiet box, from one run: its `wall`, the `cpu` seconds of its process
    and the children it waited for, and `factor`, how many times slower than a quiet core the
    phase's thread ran CPU work as it ended (tests/_drift_guard.py `pole_stretch`, measured on the
    thread: the load average read x1.8 where a test burning one core lost nothing).

    The run queue stretches CPU work, never a wait: a phase of c CPU seconds and w of waiting on
    one thread takes c x factor + w, so wall - cpu x (factor - 1) is its quiet c + w exactly. Work
    spread over several cores spends more CPU than wall and that term undershoots, so the floor is
    the wall over the factor, which no phase beats: everything in it stretched by at most that. The
    larger of the two is never above the quiet time (no load reds a test that did not grow), a
    test grown by waiting keeps its whole wall at any load, and one grown by CPU keeps its CPU."""
    stretch = max(1.0, factor)
    return max(wall / stretch, wall - max(0.0, cpu) * (stretch - 1.0))


# THE QUIET BOUND (GOH.LONGPOLEUNMEASURED). A reading taken above it is UNMEASURED: kept out of
# the table and never a verdict. `pole` corrects one stretch sample taken as the phase ended, and
# assumes waits do not stretch; I/O, lock and child-pipe waits do, and the load swings across a
# long test. test_self_hosted_hook read 61.9 s at load 47-72 on 16 cores (quiet 21.8-26 s, CPU
# identical on main and branch), and the table, shared by every land, turned every land after that
# run red (OLDINSTALL, ROADMAPIDS, NOPREBATCH, 2026-11-23). Runnable threads per core over the
# phase (tests/_drift_guard.py `run_queue`): at 1.5 a test's wall stretches at most x1.5, which
# `pole` corrects for its CPU and the 60 s line absorbs for its waits.
QUIET_LOAD = 1.5
# CPU seconds are not load-invariant on Apple silicon: more of the same work lands on efficiency
# cores, +37% at 10x load (calibrate-the-instrument, references/load-invariant-figures.md), and the
# load only ever ADDS. A loaded reading whose CPU grew past GROWTH x its quiet one grew by its code,
# at any load: 2x growth always clears it, the load alone never does.
LOAD_CPU_DRIFT = 1.37
GROWTH = 1.6


def settle(
    old: tuple[float, float | None] | None, new: tuple[float, float], loaded: bool
) -> tuple[float, float] | None:
    """A test's table entry, (quiet seconds, CPU seconds), after a run read `new` from it; None
    leaves `old` as it is. A quiet reading is the measurement. A loaded one is unmeasured, unless
    its CPU grew GROWTH x over the quiet entry's: then its quiet seconds grow by the CPU it added
    over the cores the quiet entry spread it over (CPU over quiet wall, at least one), so a real
    growth is red at any load while a loaded reading of unchanged code moves nothing."""
    if not loaded:
        return new
    if old is None or old[1] is None or old[1] <= 0:
        return None
    (was, was_cpu), cpu = (old[0], old[1]), new[1]
    if cpu < GROWTH * was_cpu:
        return None
    cores = max(1.0, was_cpu / max(was, 1e-9))
    return (round(was + (cpu - was_cpu) / cores, 3), cpu)


def standing(root: Path) -> list[str]:
    """The standing set: test files, or `file::function` ids, that every enqueue runs."""
    sys.path.insert(0, str(root / "tools"))
    try:
        import affected_tests
    finally:
        sys.path.remove(str(root / "tools"))
    return list(affected_tests.affected(root, [], enqueue=True))


def _split(nodeid: str) -> tuple[str, str]:
    """(file, function) of a cached id, without its parameters or xdist group."""
    rel, _, name = _fast()._base(nodeid).partition("::")
    return rel, name.rsplit("::", 1)[-1]


def measured(root: Path, chosen: list[str], table: dict[str, float]) -> dict[str, float]:
    """{cached id: seconds} of every test CASE the standing set runs that the tree still defines."""
    whole = {c for c in chosen if "::" not in c}
    named = {c.split("::", 1)[0] + "::" + c.rsplit("::", 1)[-1] for c in chosen if "::" in c}
    defined: dict[str, set[str]] = {}
    out: dict[str, float] = {}
    for nodeid, seconds in table.items():
        rel, name = _split(nodeid)
        if rel not in whole and f"{rel}::{name}" not in named:
            continue
        if rel not in defined:
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except OSError:
                defined[rel] = set()
            else:
                defined[rel] = {f.rsplit("::", 1)[-1] for f in _fast().functions(text)}
        if name in defined[rel]:
            out[nodeid] = seconds
    parametrized = {_split(n) for n in out if "[" in n}
    return {n: s for n, s in out.items() if "[" in n or _split(n) not in parametrized}


def load_allow(path: Path = ALLOW) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def judge(
    cases: dict[str, float],
    allow: dict[str, dict],
    limit: float = RED_S,
    running: set[str] | None = None,
) -> list[str]:
    """Every way the standing set breaks the ratchet; empty is green. `running` is the files the
    standing set runs (the measured cases' files unless named): an allowance is stale when the set
    stops RUNNING its file, never when the table has not measured it yet -- the long-pole table
    fills as runs merge into it, and a fresh one held 34 entries (GOH.LONGPOLELOAD)."""
    reds: list[str] = []
    files = {_split(n)[0] for n in cases} if running is None else running
    for rel, entry in allow.items():
        if not str(entry.get("why", "")).strip():
            reds.append(f"{rel}: the allowance carries no why")
        if float(entry.get("limit_s", 0)) <= limit:
            reds.append(f"{rel}: limit_s {entry.get('limit_s')} is no allowance over {limit:g} s")
        if rel not in files:
            reds.append(f"{rel}: the standing set no longer runs it: delete the entry")
    for nodeid, seconds in sorted(cases.items(), key=lambda kv: -kv[1]):
        rel = _split(nodeid)[0]
        cap = float(allow.get(rel, {}).get("limit_s", limit))
        if seconds > cap:
            reds.append(
                f"{nodeid}: {seconds:.1f} s over {cap:g} s, and every enqueue runs it: split it "
                "into cases xdist can spread, or declare its reads (lib/declared_reads.py)"
            )
    return reds


def poles() -> dict[str, float]:
    """The long-pole table, {node id: quiet seconds}; {} on a machine that has measured nothing."""
    return _fast().load("pole")


def check(root: Path = ROOT, table: dict[str, float] | None = None) -> list[str]:
    """The ratchet over `root`'s standing set against the long-pole table (`poles()` unless handed
    one); empty is green, and so is a machine that has measured nothing."""
    chosen = standing(root)
    cases = measured(root, chosen, poles() if table is None else table)
    running = {c.split("::", 1)[0] for c in chosen}
    return judge(cases, load_allow(root / "tests" / ALLOW.name), running=running)


def report(
    cases: dict[str, float], limit: float = LIMIT_S, allow: dict[str, dict] | None = None
) -> list[str]:
    """The longest single test, the serial sum and the tests over the limit, as lines."""
    if not cases:
        return ["no measured test in the standing set"]
    top = max(cases, key=cases.__getitem__)
    lines = [
        f"standing set: {len(cases)} measured cases, serial sum {sum(cases.values()):.0f} s",
        f"longest single test: {cases[top]:.1f} s  {top}",
    ]
    lines += [
        f"  over {limit:g} s: {s:.1f} s  {n}"
        for n, s in sorted(cases.items(), key=lambda kv: -kv[1])
        if s > limit
    ]
    for rel in allow or {}:
        mine = [s for n, s in cases.items() if _split(n)[0] == rel]
        if mine and max(mine) <= limit / 2:
            lines.append(f"  allowance for {rel} may go: its slowest test took {max(mine):.1f} s")
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--limit", type=float, default=LIMIT_S, help="the target `report` lists over")
    ap.add_argument("--check", action="store_true", help="the ratchet: exit 1 on any red")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    chosen = standing(root)
    cases = measured(root, chosen, poles())
    allow = load_allow(root / "tests" / ALLOW.name)
    print("\n".join(report(cases, args.limit, allow)))
    running = {c.split("::", 1)[0] for c in chosen}
    reds = judge(cases, allow, running=running) if args.check else []
    print("\n".join(f"✗ {r}" for r in reds))
    return 1 if reds else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
