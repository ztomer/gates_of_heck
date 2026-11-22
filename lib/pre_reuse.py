"""Whether a land pre-step verdict on a commit holds for the tip it rebased to (GOH.PRELANDTWICE).

    pre_reuse.py --root WT --judged SHA --tip SHA --base SHA [--record JUDGED_RECORD]
    pre_reuse.py --root WT --suite RECORD --base SHA   # the gate's suite (GOH.SUITEONCE, `suite`)

WHY. GOH.SPANFORGIVE's enqueue pre-step went green in 443 s at f31e3492; the lander then ran the
whole pre-land again, twice (387 s, then 478 s after main moved under it once more), and the last
move was one release commit: CHANGELOG.md and four deleted changelog/ fragments. A green verdict
on commit J says the tests its set chose passed on J's tree; the rebased tip differs from J only in
what main moved, so a moved path no test READS changes no verdict of the set.

"A test reads this path" is tools/affected_tests.py's own `readers` (THE SET's rules but the tree
readers' standing run), asked on the tip's tree: the one notion the selection and the gate's
carry (tests/_land_verdicts.py) use. The answer, one line of why first, then a node per line:

    0  REUSE   no moved path is read: the verdict judged at J stands (an empty diff too)
    3  NARROW  the moved paths are read only by the nodes listed: run those, keep J's verdict
               (`--also PATH` lines: tests the tip's set adds after a move to the pre-step's
               own code, run too; `--structural`: a move the structural gates reach)
               (a move to the binary's build inputs -- Cargo.lock, crates/ -- is NARROW too, the
               nodes the tests that run the binary, and a line `--structural` before them: the
               structural gates run the binary, so structural --full runs again; GOH.PRECODERUST)
    1  RERUN   anything else, with why -- pytest's own configuration or a conftest.py moved,
               the pre-step's declaration moved, a move to the pre-step's own code whose tip
               set cannot be compared (below), no reads table in the tree, or a diff git
               cannot give

THE PRE-STEP'S OWN CODE (GOH.PRESTEPSELFMOVE, GOH.SUITEONCEMISS): a move to it -- what chooses the
set, the pytest entry and the structural gates with all they import, source or run, and files
pytest loads before every test -- asks the TIP's selector for the tip's set, seconds, against the
set the verdict judged (the record's `chosen`, `stamp`), in the enqueue's own mode (the tree
readers whose reads are known run only when chosen: GOH.JUDGESBYREADS). A test the tip's set adds
runs (a line `--also PATH` per file, NARROW); every moved path is judged by its readers, as the
selection judges a lane's own change to the same files (a base change is the full gate's, and its
readers are the diff's: lib/test_closure.py `base`); a move the structural gates reach runs them
first (`--structural`). A set not recorded or a selector that fails runs the whole set again.
WHY: such moves were a whole re-run in 11 of 11 lander re-runs of 2026-11-21 (`.gatesrc`,
`lib/pre_reuse.py`, `lib/reuse.py`, `lib/declared_reads.py`, ...), 450-850 s each, while the land
gate beside it (GOH.GATEPARALLEL) runs the tip's diff-chosen tests under the tip's runner anyway:
its carry takes nothing from the judged tree across a runner or base move (tests/_land_verdicts.py
`choose`). What stays a whole re-run: `pyproject.toml` (pytest's own configuration), a
`conftest.py`, the data pytest's base opens as it loads (every test reads it), and a `.gatesrc`
whose GOH_LAND_PRE (the pre-step's declaration) moved.

A merged file -- one main moved and the branch changes too -- is judged as any moved path is, by
the tests that read it (GOH.PRESTEPMOVED). The tree readers are not re-run here: each file the tip
holds is J's (judged by its set), main's (judged by main's gate) or a merge of both, the full gate
runs every tree reader on the tip after, and the record kept for it carries only what the diff
J..tip does not choose. Pinned by tests/test_pre_reuse.py.

THE GATE'S SUITE (GOH.SUITEONCE): `--suite` answers 0 (the reused verdict's id on line 2) when the
gate's checkout is the very commit tree the record judged, against the gate's base, and main's
recorded tree carries every other test; tools/pytest.sh then runs no pytest. Pinned by
tests/test_suite_once.py.
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
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REUSE, RERUN, NARROW = 0, 1, 3
# The pre-step's own code (`pre_step_code`): what chooses the set, the pytest entry and the
# structural gates it runs first, with everything they import, source or run -- read from the tip's
# own sources, never a directory prefix. `gates/` and `checks/` whole were 1 of 2 in-land reruns
# of GOH.LOADSOURCE's land (checks/cad_bambu.py, 607 s) and COVOFFLAND's ~17 min (push_gate.sh),
# and the prefix never saw the selection's own lib/ modules (GOH.PRESTEPMOVED).
PRE_CODE = (".gatesrc", "pyproject.toml")
PRE_ROOTS = ("tools/affected_tests.py", "tools/pytest.sh", "gates/structural.sh")
PRE_CHECKS = "checks/check_"  # the structural binary runs these by name (crates/goh steps)
STRUCTURAL = "--structural"  # NARROW's line after the why: the structural gates run again
ALSO = "--also"  # NARROW's line per test file the tip's set adds (GOH.SUITEONCEMISS)
PRE_DECL = "GOH_LAND_PRE"  # the pre-step's declaration in .gatesrc: a move there reruns the set
SELECTOR = "tools/affected_tests.py"  # what chooses the set (GOH.PRESTEPSELFMOVE)


def _diff(root: Path, a: str, b: str) -> list[str] | None:
    r = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "--no-renames", a, b],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )  # fmt: skip
    return None if r.returncode else [p for p in r.stdout.splitlines() if p]


def _table(root: Path):
    """The tip's own tools/affected_tests.py, or None when it has no `readers`."""
    if not (root / "tools" / "affected_tests.py").is_file():
        return None

    def ours(m: str) -> bool:
        return m in ("affected_tests", "lib") or m[:4] == "lib."

    # The tip's table, never one an earlier root loaded -- and given back after: left in place, the
    # tip's `affected_tests` and `lib.*` were the CALLER's from then on (a suite worker's later
    # `import affected_tests` got a fixture's table with no `affected`, CI on v0.138.0).
    kept = {m: sys.modules.pop(m) for m in [m for m in sys.modules if ours(m)]}
    sys.path.insert(0, str(root / "tools"))
    try:
        mod = importlib.import_module("affected_tests")
    except Exception:  # noqa: BLE001 -- a table that does not load is no table: re-run
        return None
    finally:
        del sys.path[0]
        for name in [m for m in sys.modules if ours(m)]:
            del sys.modules[name]
        sys.modules.update(kept)
    return mod if hasattr(mod, "readers") else None


def judged_set(record: Path | None) -> list[str] | None:
    """The set a kept verdict's record says it judged (`chosen`, `stamp`); None: not recorded."""
    try:
        got = json.loads(record.read_text(encoding="utf-8")).get("chosen") if record else None
    except (OSError, ValueError, AttributeError):
        return None
    ok = isinstance(got, list) and all(isinstance(n, str) for n in got)
    return got if ok else None


def _plural(n: int, what: str) -> str:
    return f"{n} {what}" + ("s" if n != 1 else "")


def within(root: Path, table, tip: str, base: str,
           judged: list[str] | None) -> tuple[list[str] | None, str]:  # fmt: skip
    """(the tests the tip's selector chooses that the verdict did not judge, why); None when it
    cannot tell (GOH.PRESTEPSELFMOVE): the tip's `affected` over its diff from main in the
    enqueue's mode (the judged set's own), each node judged by itself or its file."""
    if judged is None:
        return None, "the set the verdict judged is not recorded (its record has no `chosen`)"
    if not hasattr(table, "affected"):
        return None, f"the tip's {SELECTOR} chooses no set (no `affected`)"
    mb = _git(root, "merge-base", base, tip)
    changed = _diff(root, mb, tip) if mb else None
    if changed is None:
        return None, f"git cannot diff main {base[:8]}...{tip[:8]}"
    try:
        try:
            chosen = list(table.affected(root, changed, enqueue=True))
        except TypeError:  # a table older than the enqueue's mode (GOH.JUDGESBYREADS) has one
            chosen = list(table.affected(root, changed))
    except Exception as e:  # noqa: BLE001 -- a selector that fails chose nothing to compare
        return None, f"the tip's selector failed ({e.__class__.__name__}: {e})"
    held = set(judged)
    if added := [n for n in chosen if n not in held and n.split("::")[0] not in held]:
        more = f" (+{len(added) - 5} more)" if len(added) > 5 else ""
        return added, (f"the tip's set adds {_plural(len(added), 'test')} the verdict did not "
                       f"judge, run too: {' '.join(added[:5])}{more}")  # fmt: skip
    return [], f"the tip's set ({_plural(len(chosen), 'test')}) is within the {len(held)} judged"


def decide(
    root: Path, judged: str, tip: str, base: str, record: Path | None = None
) -> tuple[int, str, list[str]]:
    """(REUSE|NARROW|RERUN, why, the nodes NARROW runs); RECORD is the verdict's (`judged_set`)."""
    return _decide(root, judged, tip, base, record)[:3]


def _decide(
    root: Path, judged: str, tip: str, base: str, record: Path | None = None
) -> tuple[int, str, list[str], list[str]]:
    """`decide`, and the test files the tip's set adds, which NARROW runs too (`--also`)."""
    moved = _diff(root, judged, tip)
    if moved is None:
        return RERUN, f"git cannot diff {judged[:8]}..{tip[:8]}", [], []
    if not moved:
        return REUSE, f"the tree judged at {judged[:8]} is {tip[:8]}'s", [], []
    n = f"{len(moved)} moved path" + ("s" if len(moved) != 1 else "")
    shown = " ".join(moved[:8]) + (f" (+{len(moved) - 8} more)" if len(moved) > 8 else "")
    own = _diff(root, base, tip)
    if own is None:
        return RERUN, f"git cannot diff main {base[:8]}..{tip[:8]}", [], []
    # A merged file -- main moved it and the branch changes it -- is in neither judged tree: its
    # readers judge its bytes like any moved path's (GOH.PRESTEPMOVED: docs/map.md, named by 10
    # test files, ran GOH.LOADSOURCE's whole set again, 466 s). The tree readers' verdict on them
    # is the full gate's, which runs every tree reader on every diff (tests/_land_verdicts.py).
    merged = sorted(set(moved) & set(own))
    if merged:
        n += f" ({len(merged)} merged: {' '.join(merged[:3])}{' ...' if len(merged) > 3 else ''})"
    if "pyproject.toml" in moved:
        return RERUN, "pyproject.toml moved: pytest's own configuration", [], []
    if wide := [p for p in moved if Path(p).name == "conftest.py"]:
        return RERUN, f"{wide[0]} moved: pytest loads it before every test", [], []
    if ".gatesrc" in moved and _declared(root, judged) != _declared(root, tip):
        return RERUN, f"{PRE_DECL} moved in .gatesrc: the pre-step's own declaration", [], []
    table = _table(root)
    if table is None:
        return RERUN, "no reads table (tools/affected_tests.py readers) in the tree", [], []
    graph = table.Closure(root)  # built once: the pre-step's code and the readers both read it
    # The DATA pytest's base opens as it loads (lib/test_closure.py `load_reads`): every test reads
    # it, so the selection chooses every test for a change to it, and so does this.
    loads = graph.load_reads() if hasattr(graph, "load_reads") else {}
    if wide := sorted(set(moved) & set(loads)):
        return RERUN, f"{wide[0]} moved: pytest loads it before every test", [], []
    lead, also = "", []
    # The pre-step's own code (GOH.PRESTEPSELFMOVE, GOH.SUITEONCEMISS: module doc): the tip's set
    # against the judged one; what it adds runs, and the moved files are judged by their readers.
    # The binary's build inputs are not the runner's code (GOH.PRECODERUST): their readers are
    # the tests that run the binary (`binary_runners.choose` is in `readers`), and `--structural`.
    if code := pre_step_code(root, table, moved, graph, binary=False):
        added, why = within(root, table, tip, base, judged_set(record))
        if added is None:
            return RERUN, f"{code[0]} moved: the pre-step's own code, and {why}", [], []
        lead, also = f"{code[0]} moved: the pre-step's own code, and {why}; ", added
    read = table.readers(root, moved)
    if read or also or _built(root, table, moved) or structural(root, table, moved, graph):
        nodes = sorted(set(read) | set(also))
        return (
            NARROW,
            f"{lead}{n}, read by {len(read)} test file{'s' if len(read) != 1 else ''}: {shown}",
            nodes,
            sorted({x.split("::")[0] for x in also}),
        )
    return REUSE, f"{lead}{n}, none read by a test: {shown}", [], []


def _declared(root: Path, rev: str) -> list[str]:
    """The lines of REV's .gatesrc that declare the pre-step (PRE_DECL), as text."""
    r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:.gatesrc"], capture_output=True,
                       text=True, check=False, stdin=subprocess.DEVNULL,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})  # fmt: skip
    decl = re.compile(rf"^\s*(?:export\s+)?{PRE_DECL}=")
    return (
        [ln.strip() for ln in r.stdout.splitlines() if decl.match(ln)] if not r.returncode else []
    )


def structural(root: Path, table, moved: list[str], graph=None) -> bool:
    """A move the structural gates reach (gates/structural.sh, the checks the binary runs, all they
    import, source or run): the narrow run runs structural --full first, as the whole set does."""
    graph = table.Closure(root) if graph is None else graph
    if not hasattr(graph, "reach"):
        return False
    own: set[str] = set()
    for rel in (r for r in _roots(graph) if r not in (SELECTOR, "tools/pytest.sh")):
        own |= {rel, *graph.reach(rel, skip=set())[0]}
    return any(p in own for p in moved)


def pre_step_code(
    root: Path, table, moved: list[str], graph=None, binary: bool = True
) -> list[str]:
    """Each of `moved` that is the pre-step's own code at `root` (GOH.PRESTEPMOVED): PRE_CODE, the
    PRE_ROOTS and the checks the structural binary runs (PRE_CHECKS), with all they import, source
    or run as `table`'s graph reads it (lib/test_closure.py `reach`, the selection's one dependency
    reader), and the native binary's build inputs, which the structural gates run (BINARY: the
    runner's code alone without them, GOH.PRECODERUST: `decide` re-runs structural and the tests
    that run the binary for a move there, not the set). An edge a move
    adds or drops is a change to a file the graph reaches, so the tip's graph alone misses none.
    A table whose graph cannot reach answers with the roots alone."""
    graph = table.Closure(root) if graph is None else graph
    roots = _roots(graph)
    own = {*PRE_CODE, *PRE_ROOTS, *roots}
    for rel in roots if hasattr(graph, "reach") else ():
        own.update(graph.reach(rel, skip=set())[0])
    built = _built(root, table, moved) if binary else set()
    return [p for p in moved if p in own or p in built]


def _roots(graph) -> list[str]:
    texts = getattr(graph, "texts", {})
    roots = [r for r in PRE_ROOTS if r in texts]
    return roots + sorted(p for p in texts if p.startswith(PRE_CHECKS) and p.endswith(".py"))


def _built(root: Path, table, moved: list[str]) -> set[str]:
    builds = getattr(getattr(table, "binary_runners", None), "build_inputs_in", None)
    return set(builds(root, moved)) if builds else set()


def _kit(name: str, sub: str):
    """A module of this kit's own (lib/, tests/), imported once."""
    if name in sys.modules:
        return sys.modules[name]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / sub))
    try:
        return importlib.import_module(name)
    finally:
        del sys.path[0]


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                       check=False, stdin=subprocess.DEVNULL,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})  # fmt: skip
    return r.stdout.strip() if r.returncode == 0 else ""


def base_sha(root: Path, base: str) -> str | None:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return out.stdout.strip() or None


def stamp(record: str, base: str | None, chosen: list[str] | None = None) -> None:
    """The record says it is the WHOLE selection the diff from BASE chose (GOH.SUITEONCE): the gate
    of the tree it judged takes it for the suite only then (lib/pre_reuse.py `suite`). With CHOSEN,
    it names that set (`chosen`), which a move to the pre-step's own code is held to (`within`),
    and the MODE it was chosen in (`mode`: `enqueue` under GOH_LAND_PRE_ONLY, else `gate`), which
    the gate's carry shadow asks in (tests/_carry_shadow.py, GOH.CARRYSHADOW)."""
    if not base or not record or record == os.devnull:
        return
    try:
        rec = json.loads(Path(record).read_text())
    except (OSError, ValueError):
        return
    if isinstance(rec, dict):
        named = {"chosen": sorted(chosen)} if chosen is not None else {}
        mode = "enqueue" if os.environ.get("GOH_LAND_PRE_ONLY") else "gate"
        Path(record).write_text(
            json.dumps({**rec, "base": base, "selection": "affected", "mode": mode, **named})
        )


def suite(root: Path, record: Path, base: str) -> tuple[bool, str, str]:
    """(the gate's suite may take the pre-step's verdict instead of a run, why, the verdict's id)
    (GOH.SUITEONCE). It may when the record is the pre-step's WHOLE selection (tools/
    affected_tests.py stamps `selection` and the `base` it chose against) on exactly this checkout's
    commit tree and tools (lib/gate_identity.py `suite`, which a dirty checkout has none of), that
    base is the gate's, and the store holds a green suite record of the base's tree whose every
    test is either judged by the record or carried across the diff (tests/_land_verdicts.py
    `carried`: affected_tests does not choose it). Never under the coverage step's instrumented run,
    whose profiles a skipped test would not leave. Each reuse is one use of the audited `carry`
    path; on a reuse the tree's own suite record is written, so the next land carries from it."""
    if all(os.environ.get(k) for k in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE")):
        return False, "the instrumented coverage run needs its own profiles", ""
    try:
        rec = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"no pre-step record ({e.__class__.__name__})", ""
    if not isinstance(rec, dict) or not isinstance(rec.get("passed"), dict):
        return False, "a malformed pre-step record", ""
    if rec.get("selection") != "affected" or not isinstance(rec.get("base"), str):
        return False, "the record is not the pre-step's whole selection", ""
    gi, reuse = _kit("gate_identity", "lib"), _kit("reuse", "lib")
    here = gi.suite(root)
    if here is None:
        return False, "the checkout is not its commit's tree (a change, an untracked file)", ""
    if rec.get("identity") != here:
        return False, "the pre-step judged another tree or toolchain", ""
    if rec["base"] != base:
        return (
            False,
            f"the pre-step chose against {rec['base'][:8]}, the gate's base is {base[:8]}",
            "",
        )
    if _git(root, "merge-base", base, "HEAD") != base:
        return False, f"{base[:8]} is not below HEAD", ""
    tree, tools = _git(root, "rev-parse", "HEAD^{tree}"), gi.suite_tools()
    main_tree = _git(root, "rev-parse", f"{base}^{{tree}}")
    got, why = reuse.lookup_suite(root, main_tree, tools) if main_tree else (None, "no tree")
    if got is None or got.get("verdict") != "green":
        return False, f"no suite record of main {base[:8]}'s tree to stand on ({why})", ""
    changed = _diff(root, base, "HEAD")
    table = _table(root)
    if changed is None or table is None:
        return False, "no diff from main, or no selection table in the tree", ""
    lv = _kit("_land_verdicts", "tests")
    chosen, why = lv.choose(root, changed, table)
    if chosen is None:
        return False, f"nothing carries across the diff: {why}", ""
    mains = {n: bool(b) for n, b in got["passed"].items()}
    carried = lv.carried(mains, chosen, False, lv._builds(changed, root))
    union = {**{n: mains[n] for n in carried}, **{n: bool(b) for n, b in rec["passed"].items()}}
    if left := sorted(n for n in mains if n not in union and (root / n.split("::")[0]).is_file()):
        more = f" (+{len(left) - 1} more)" if len(left) > 1 else ""
        return False, f"{left[0]}{more} passed on main is neither judged here nor carried", ""
    fields = reuse.carry_fields(root, main_tree, tools)
    if (use := _kit("reuse_audit", "lib").use("carry", fields)) != "reuse":
        return False, f"the audited `carry` path says {use!r}: the suite runs", ""
    # Main's measurement of each node it carries (GOH.COVNONBIN): one that ran no binary there ran
    # none here, so the next instrumented land carries it; the pre-step's own ran plain, unmeasured.
    seen = got.get("measured") if isinstance(got.get("measured"), dict) else {}
    measured = dict.fromkeys((n for n in carried if seen.get(n) is False), False)
    reuse.record_suite(root, tree, tools, union, set(), False, "land gate (suite reused)",
                       len(carried), measured=measured, judged=set(rec["passed"]))  # fmt: skip
    vid = f"{str(rec.get('sha') or '')[:12]}:{here[:12]}"
    return True, (f"the pre-step judged this tree ({len(rec['passed'])} tests), main "
                  f"{base[:8]}'s record carries {len(carried)}"), vid  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--suite", metavar="RECORD", help="the gate's suite: may it take RECORD?")
    for flag in ("--judged", "--tip"):
        ap.add_argument(flag)
    ap.add_argument("--base", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--record", help="the judged verdict's record: the set it judged (`chosen`)")
    args = ap.parse_args(argv)
    if args.suite:
        ok, why, vid = suite(Path(args.root).resolve(), Path(args.suite), args.base)
        print(why)
        if ok:
            print(vid)
        return REUSE if ok else RERUN
    if not (args.judged and args.tip):
        ap.error("--judged and --tip are required without --suite")
    record = Path(args.record) if args.record else None
    root = Path(args.root).resolve()
    code, why, nodes, also = _decide(root, args.judged, args.tip, args.base, record)
    print(why)
    table = _table(root) if code == NARROW else None
    moved = _diff(root, args.judged, args.tip) or [] if table is not None else []
    if table is not None and (_built(root, table, moved) or structural(root, table, moved)):
        print(STRUCTURAL)  # the binary's build or the structural gates moved: structural --full
    for f in also:
        print(f"{ALSO} {f}")  # a test file the tip's set adds: the narrow run runs it too
    for n in nodes:
        print(n)
    return code


if __name__ == "__main__":
    sys.exit(main())
