"""Whether a land pre-step verdict on a commit holds for the tip it rebased to (GOH.PRELANDTWICE).

    pre_reuse.py --root WT --judged SHA --tip SHA --base SHA
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
    1  RERUN   anything else, with why -- a moved path the branch changes too (the merged file
               is in neither judged tree), the pre-step's own code or a suite-wide input moved,
               no reads table in the tree, or a diff git cannot give

The tree readers are not re-run here: each file the tip holds is J's (judged by its set) or main's
(judged by main's gate), the full gate runs them on the tip after, and the record kept for it
carries only what the diff J..tip does not choose. Pinned by tests/test_pre_reuse.py.

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
import subprocess
import sys
from pathlib import Path

REUSE, RERUN, NARROW = 0, 1, 3
# The pre-step's own code: what chooses the set and the structural gates it runs first.
PRE_CODE = (".gatesrc", "pyproject.toml", "tools/pytest.sh", "tools/affected_tests.py")
PRE_DIRS = ("gates/", "checks/")


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


def decide(root: Path, judged: str, tip: str, base: str) -> tuple[int, str, list[str]]:
    """(REUSE|NARROW|RERUN, why, the nodes NARROW runs)."""
    moved = _diff(root, judged, tip)
    if moved is None:
        return RERUN, f"git cannot diff {judged[:8]}..{tip[:8]}", []
    if not moved:
        return REUSE, f"the tree judged at {judged[:8]} is {tip[:8]}'s", []
    n = f"{len(moved)} moved path" + ("s" if len(moved) != 1 else "")
    shown = " ".join(moved[:8]) + (f" (+{len(moved) - 8} more)" if len(moved) > 8 else "")
    own = _diff(root, base, tip)
    if own is None:
        return RERUN, f"git cannot diff main {base[:8]}..{tip[:8]}", []
    if both := sorted(set(moved) & set(own)):
        return RERUN, f"{both[0]} moved on main and is changed by the branch: a merged file", []
    code = [p for p in moved if p in PRE_CODE or p.startswith(PRE_DIRS)]
    code += [p for p in moved if Path(p).name == "conftest.py"]
    if code:
        return RERUN, f"{code[0]} moved: the pre-step's own code or a suite-wide input", []
    table = _table(root)
    if table is None:
        return RERUN, "no reads table (tools/affected_tests.py readers) in the tree", []
    if wide := sorted(set(moved) & table.Closure(root).base()):
        return RERUN, f"{wide[0]} moved: pytest loads it before every test", []
    read = table.readers(root, moved)
    if read:
        return (
            NARROW,
            f"{n}, read by {len(read)} test file{'s' if len(read) != 1 else ''}: {shown}",
            sorted(read),
        )
    return REUSE, f"{n}, none read by a test: {shown}", []


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


def stamp(record: str, base: str | None) -> None:
    """The record says it is the WHOLE selection the diff from BASE chose (GOH.SUITEONCE): the gate
    of the tree it judged takes it for the suite only then (lib/pre_reuse.py `suite`)."""
    if not base or not record or record == os.devnull:
        return
    try:
        rec = json.loads(Path(record).read_text())
    except (OSError, ValueError):
        return
    if isinstance(rec, dict):
        Path(record).write_text(json.dumps({**rec, "base": base, "selection": "affected"}))


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
    reuse.record_suite(root, tree, tools, union, set(), False, "land gate (suite reused)",
                       len(carried))  # fmt: skip
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
    args = ap.parse_args(argv)
    if args.suite:
        ok, why, vid = suite(Path(args.root).resolve(), Path(args.suite), args.base)
        print(why)
        if ok:
            print(vid)
        return REUSE if ok else RERUN
    if not (args.judged and args.tip):
        ap.error("--judged and --tip are required without --suite")
    code, why, nodes = decide(Path(args.root).resolve(), args.judged, args.tip, args.base)
    print(why)
    for n in nodes:
        print(n)
    return code


if __name__ == "__main__":
    sys.exit(main())
