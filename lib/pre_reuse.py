"""Whether a land pre-step verdict on a commit holds for the tip it rebased to (GOH.PRELANDTWICE).

    pre_reuse.py --root WT --judged SHA --tip SHA --base SHA

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
    sys.path.insert(0, str(root / "tools"))
    try:
        for name in [m for m in sys.modules if m in ("affected_tests", "lib") or m[:4] == "lib."]:
            del sys.modules[name]  # the tip's table, never one an earlier root loaded
        mod = importlib.import_module("affected_tests")
    except Exception:  # noqa: BLE001 -- a table that does not load is no table: re-run
        return None
    finally:
        del sys.path[0]
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for flag in ("--judged", "--tip", "--base"):
        ap.add_argument(flag, required=True)
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    code, why, nodes = decide(Path(args.root).resolve(), args.judged, args.tip, args.base)
    print(why)
    for n in nodes:
        print(n)
    return code


if __name__ == "__main__":
    sys.exit(main())
