"""The tests a land's gate would run only because nothing judged them, run before its lock.

GOH.PREUNDECL. The gate's suite takes the enqueue's verdict when every test main passed is judged
on this very tree or carried from main's record (lib/pre_reuse.py `suite`, GOH.SUITEONCE). 27 lands
of 2026-11-22..25 missed it by a few hundred tests and ran them under the lander's lock. The 14
whose records still stood, replayed, missed them for three reasons, half the lands for two:

    refused   main's pass read a path the diff changes that no rule of the selector declares, or
              was never watched (tests/_read_audit.py `undeclared`): 14 lands, 63-732 tests each
    binary    it ran the native binary and the diff changes the build: 2 lands, 253 and 911
    gone      no test of this tree: deleted, or its parameters renamed, in a file the diff changes
              -- main passed it and nothing here ever will, so no pre-step could save that land: 7

(A test that ran here and was skipped is no pass either; no replayed land had one.)

So the enqueue's pre-step asks the gate's own question (`gap`, the one `suite` asks) right after its
set is green, and answers it in its own slot (`run`): the tests left are collected, what this tree
holds is run (carrying like any pre-step run), and its passes join the record and the store.
`settled` on the record names every test so answered -- it passed, was skipped, or is not collected
here -- and `gap` leaves it out. Nothing is carried that was refused: a refused test RUNS, a red one
is the pre-step's red, and the record's identity (the tree and the tools) is checked by the gate
before any of it is read. A collection that cannot be read whole, a run on a tree that moved, or
more than MOST tests settles nothing: the gate runs them, as before.

Only the enqueue runs it (GOH_LAND_PRE_ONLY): a lane's own run answers the lane, and a pre-land
under the lock has the whole suite beside it. Pinned by tests/test_pre_owed.py.

GOH.PREUNDECL: the tests main passed that a land's gate could not take as judged (a read no rule
declares, the binary's build, a test deleted or renamed), run by the enqueue's pre-step outside the
lander's lock and recorded (`settled`), so the gate's suite takes the verdict (`pre_reuse.py`
`suite`, which asks the same `gap`).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_reuse  # lib/, beside this file: the one reader of the tree's selection

RECORD, REUSE = "GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE"  # tests/_land_verdicts.py
ENQUEUE = "GOH_LAND_PRE_ONLY"  # the enqueue's pre-step (tools/land.sh)
SETTLED = "settled"  # the record's key: tests answered on its tree that passed none
MOST = 1500  # tests; past it the gate's own run is the cheaper half (1,353 was the most replayed)
COUNT = re.compile(r"^(\d+) tests? collected\b", re.MULTILINE)


class Gap(NamedTuple):
    """What main's record and the pre-step's say of a tree (`gap`); LEFT is what neither does."""

    left: list[str]
    why: list[str]  # the carry's own lines: which read refused how many
    rec: dict
    got: dict  # main's suite record
    own: dict  # this tree's, before the gate
    tree: str
    main_tree: str
    tools: str
    here: str
    mains: dict[str, bool]
    carried: set[str]
    union: dict[str, bool]


def gap(root: Path, record: Path, base: str) -> tuple[Gap | None, str]:
    """(what main's record of BASE passed that the pre-step's RECORD does not answer on this very
    checkout, ""), or (None, why the record is no verdict on it at all): lib/pre_reuse.py `suite`'s
    question, asked by the gate and, before it, by the enqueue's pre-step (`run`)."""
    kit, git = pre_reuse._kit, pre_reuse._git
    try:
        rec = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"no pre-step record ({e.__class__.__name__})"
    if not isinstance(rec, dict) or not isinstance(rec.get("passed"), dict):
        return None, "a malformed pre-step record"
    if rec.get("selection") != "affected" or not isinstance(rec.get("base"), str):
        return None, "the record is not the pre-step's whole selection"
    # GOH.REMOTETRUST (lib/remote_trust.py): the worker's passes count for platform_agnostic tests
    trusted, mac = kit("remote_trust", "lib").split(root, rec.get("remote") or [])
    if mac:  # macos_only: a Linux pass is another toolchain's verdict, the suite runs on the Mac
        return None, (f"macos_only tests passed on the Linux worker: {len(mac)}, "
                      f"{mac[0]} first: the suite runs on the Mac")  # fmt: skip
    gi, reuse = kit("gate_identity", "lib"), kit("reuse", "lib")
    here = gi.suite(root)
    if here is None:
        return None, "the checkout is not its commit's tree (a change, an untracked file)"
    if rec.get("identity") != here:
        return None, "the pre-step judged another tree or toolchain"
    if rec["base"] != base:
        return None, f"the pre-step chose against {rec['base'][:8]}, the gate's base is {base[:8]}"
    if git(root, "merge-base", base, "HEAD") != base:
        return None, f"{base[:8]} is not below HEAD"
    tree, tools = git(root, "rev-parse", "HEAD^{tree}"), gi.suite_tools()
    main_tree = git(root, "rev-parse", f"{base}^{{tree}}")
    got, why = reuse.lookup_suite(root, main_tree, tools) if main_tree else (None, "no tree")
    if got is None or got.get("verdict") != "green":
        return None, f"no suite record of main {base[:8]}'s tree to stand on ({why})"
    changed = pre_reuse._diff(root, base, "HEAD")
    table = pre_reuse._table(root)
    if changed is None or table is None:
        return None, "no diff from main, or no selection table in the tree"
    lv = kit("_land_verdicts", "tests")
    mode = kit("_carry_shadow", "tests").mode_of(str(record))  # GOH.CARRYBASECODE
    chosen, why = lv.choose(root, changed, table, mode)
    if chosen is None:
        return None, f"nothing carries across the diff: {why}"
    mains = {n: bool(b) for n, b in got["passed"].items()}
    carried = lv.carried(mains, chosen, False, lv._builds(changed, root))
    said: list[str] = []
    carried -= lv._read_audit.undeclared(said, got, carried, changed, base)  # an undeclared read
    # ... and what the pre-step CARRIED onto this tree: its store record's (GOH.READKEYVERDICT)
    own = reuse.lookup_suite(root, tree, tools)[0] or {}
    ran = {**dict.fromkeys(trusted, False), **{n: bool(b) for n, b in rec["passed"].items()}}
    took = {n: bool(b) for n, b in own.get("passed", {}).items() if n not in ran}
    carried = carried | set(took)
    union = {**{n: mains[n] for n in carried if n in mains}, **took, **ran}
    answered = set(rec.get(SETTLED) or ())  # on this very tree: RECORD's identity is HERE
    left = sorted(n for n in mains if n not in union and n not in answered
                  and (root / n.split("::")[0]).is_file())  # fmt: skip
    return Gap(left, said, rec, got, own, tree, main_tree, tools, here, mains, carried, union), ""


def collected(root: Path, nodes: list[str]) -> set[str] | None:
    """The NODES this tree's suite collects; None when the collection is red or does not add up:
    a test missing from a list that is not whole is no test that is gone."""
    files = sorted({n.split("::")[0] for n in nodes})
    env = {k: v for k, v in os.environ.items() if k not in (RECORD, REUSE)}
    r = subprocess.run(
        ["bash", str(root / "tools" / "pytest.sh"), "--collect-only", "-n", "0", *files],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, env=env,
    )  # fmt: skip
    if r.returncode == 5 and "no tests collected" in r.stdout:  # pytest's: every one is gone
        return set()
    if r.returncode:
        return None
    ids = {ln for ln in r.stdout.splitlines() if "::" in ln and ln.split("::")[0] in files}
    said = COUNT.search(r.stdout)
    return ids & set(nodes) if said and int(said[1]) == len(ids) else None


def settle(record: Path, owed: Path | None, left: list[str]) -> bool:
    """RECORD answers LEFT: the green run's own record OWED (None: nothing was there to run) joins
    it when both judged one tree and commit; False, RECORD untouched, when they did not."""
    rec = json.loads(record.read_text(encoding="utf-8"))
    if owed is not None:
        try:
            got = json.loads(owed.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        mine, its = ((r.get("identity"), r.get("sha")) for r in (rec, got))
        if mine[0] is None or mine != its:
            return False
        rec["passed"] = {**rec["passed"], **got["passed"]}
        reads = rec.get("reads") or {}
        for f, paths in (got.get("reads") or {}).items():
            reads[f] = sorted({*reads.get(f, ()), *paths})
        rec["reads"] = reads
    rec[SETTLED] = sorted({*(rec.get(SETTLED) or ()), *left})
    tmp = Path(f"{record}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    tmp.replace(record)
    return True


def run(root: Path, base: str | None) -> int:
    """The enqueue's pre-step, its set green: the tests the gate would run for want of a verdict,
    run now (module doc); their exit. 0 with nothing settled wherever the gate must decide."""
    record = os.environ.get(RECORD, "")
    if not base or not record or record == os.devnull or not os.environ.get(ENQUEUE):
        return 0
    from tui.lib import counted, info, ok  # at run time: lib/ alone imports this module too

    was = {k: os.environ[k] for k in (RECORD, REUSE) if k in os.environ}
    found, why = gap(root, Path(record), base)
    os.environ.update(was)  # tests/_land_verdicts.py takes them out as it loads (`_take`)
    if found is None or not found.left:
        info(f"pre-land: {why or 'every test main passed is judged here or carried'}")
        return 0
    left = found.left
    for line in found.why:
        info(f"pre-land: {line}")
    said = f"{counted(len(left), 'test')} main passed that nothing here answers"
    if len(left) > MOST:
        info(f"pre-land: {said}: more than {MOST}, the gate runs them")
        return 0
    here = collected(root, left)
    if here is None:
        info(f"pre-land: {said}: the collection cannot be read whole, the gate runs them")
        return 0
    owed = Path(f"{record}.owed")
    try:
        if here:
            from lib.step_timings import timed

            argv = ["env", f"{REUSE}={os.devnull}", f"{RECORD}={owed}", "bash",
                    str(root / "tools" / "pytest.sh"), "-q", *sorted(here)]  # fmt: skip
            if rc := timed(f"pre-land: {len(here)} tests the gate would run", argv):
                return rc
        if not settle(Path(record), owed if here else None, left):
            info(f"pre-land: {said}: the tree moved under their run, the gate runs them")
            return 0
    finally:
        owed.unlink(missing_ok=True)
    ok(f"pre-land: {said}: {len(here)} run here, {len(left) - len(here)} gone from this tree")
    return 0
