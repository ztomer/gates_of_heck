"""lib/long_pole.py -- the longest test every enqueue runs (GOH.PRELONGPOLE).

    long_pole.py [--root DIR] [--limit S]    # the longest test, the serial sum, the offenders

THE STANDING SET is what `goh land --enqueue`'s pre-step chooses for an EMPTY diff
(`tools/affected_tests.py` `affected(root, [], enqueue=True)`): the judges whose reads are not
known, which every land pays whatever it changes. Its longest single test is the floor under every
land's pre-step, whatever the worker count: xdist cannot split one test (tests/test_self_walk.py's
two whole-tree walks were 147 s, tests/test_land_check.py's replay 98 s, on 2026-11-21), so the
10-minute land plan needs each of them short, or its reads declared so a diff that cannot move
them does not run them (lib/declared_reads.py).

THE TABLE is the suite's own duration table (`fast_tests.load()`, written by tools/pytest.sh), keyed
by node id with its parameters, so a parametrized test's cases are judged one by one: under xdist a
case is a test of its own. A cached id the tree no longer defines is not judged (the table prunes
a deleted file, never a deleted function), and an id with no parameters beside the same function's
parametrized ones is the stale one from before the split.

THE RATCHET is `tests/long_pole_allow.json`: file -> {"limit_s", "why"}, today's tests over the
LIMIT_S that could not be cut yet. It only shrinks (tests/test_long_pole.py): a test over its
file's limit, or over LIMIT_S with no entry, is red; an entry whose tests are all under LIMIT_S, or
for a file the standing set no longer holds, is stale and red.
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

LIMIT_S = 30.0  # the most one test of the standing set may take, measured
ALLOW = ROOT / "tests" / "long_pole_allow.json"


def _fast():
    """lib/fast_tests.py, the duration table's one reader (it imports from the root)."""
    sys.path.insert(0, str(ROOT))
    try:
        import fast_tests
    finally:
        sys.path.remove(str(ROOT))
    return fast_tests


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


def judge(cases: dict[str, float], allow: dict[str, dict], limit: float = LIMIT_S) -> list[str]:
    """Every way the standing set breaks the ratchet; empty is green."""
    reds: list[str] = []
    files = {_split(n)[0] for n in cases}
    for rel, entry in allow.items():
        if not str(entry.get("why", "")).strip():
            reds.append(f"{rel}: the allowance carries no why")
        if float(entry.get("limit_s", 0)) <= limit:
            reds.append(f"{rel}: limit_s {entry.get('limit_s')} is no allowance over {limit:g} s")
        if rel not in files:
            reds.append(f"{rel}: the standing set no longer runs it: delete the entry")
        elif all(s <= limit for n, s in cases.items() if _split(n)[0] == rel):
            reds.append(f"{rel}: every test is under {limit:g} s now: delete the entry")
    for nodeid, seconds in sorted(cases.items(), key=lambda kv: -kv[1]):
        rel = _split(nodeid)[0]
        cap = float(allow.get(rel, {}).get("limit_s", limit))
        if seconds > cap:
            reds.append(
                f"{nodeid}: {seconds:.1f} s over {cap:g} s, and every enqueue runs it: split it "
                "into cases xdist can spread, or declare its reads (lib/declared_reads.py)"
            )
    return reds


def check(root: Path = ROOT, table: dict[str, float] | None = None) -> list[str]:
    """The ratchet over `root`'s standing set against the duration table (`fast_tests.load()`
    unless handed one); empty is green, and so is a machine that has measured nothing."""
    cases = measured(root, standing(root), _fast().load() if table is None else table)
    return judge(cases, load_allow(root / "tests" / ALLOW.name))


def report(cases: dict[str, float], limit: float = LIMIT_S) -> list[str]:
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
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--limit", type=float, default=LIMIT_S)
    ap.add_argument("--check", action="store_true", help="the ratchet: exit 1 on any red")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    cases = measured(root, standing(root), _fast().load())
    print("\n".join(report(cases, args.limit)))
    reds = judge(cases, load_allow(root / "tests" / ALLOW.name), args.limit) if args.check else []
    print("\n".join(f"✗ {r}" for r in reds))
    return 1 if reds else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
