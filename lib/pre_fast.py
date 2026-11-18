"""Whether a moved tree's land pre-step may be the fast set alone (GOH.PRESTEPDUP).

    pre_fast.py --root WT --base SHA [--external CMD]

WHY. A batch of BASETEMPSLOT and SUITEONCE (2026-11-18) stacked the lanes on main, which changed
the tip's tree, so the lander ran the whole pre-step on it again (353 s, 508 tests), holding the
land lock; then the gate ran the INSTRUMENTED coverage suite (665 s), which re-runs every test the
pre-step ran (a same-tree pass is skipped under coverage only once an instrumented run measured it
running no binary: the record of that tree says 6353 run, 0 carried). Under the lock the pre-step
buys no fail-fast the gate does not give, and its verdict buys nothing the instrumented gate takes.

It still buys something on the PLAIN path: a gate whose Rust coverage measurement is reused runs
the suite plain, and that suite takes the pre-step's whole verdict on the same tree instead of a run
(GOH.SUITEONCE, lib/pre_reuse.py `suite`). So the question is the gate's: will it MEASURE?

It measures when the tree's measurement inputs differ from main's, whose gate left a measurement
of them (gates/_rust_proven.sh rust_coverage_reuse): the paths lib/gate_identity.py `rust` keys
(the Rust build inputs, the coverage scripts, the external suite's runner), the diff coverage
allowlist, and .gatesrc (the GOH_COV_* settings). A diff main..tip that touches one answers 0,
MEASURES: the fast set suffices. Anything else answers 1, MAY REUSE: the whole set runs, as before.
No external suite (GOH_COV_RUST_EXTERNAL) answers 1; a Rust identity that cannot be made (a
workspace member outside the keyed inputs: the gate always measures) answers 0.

A wrong answer costs time, never a verdict: the gate runs the whole suite either way (instrumented,
or plain and carried), and a fast-set record is never a whole selection (tools/affected_tests.py
--fast-only stamps none), so no gate reuses it. Unseen here and so answered 1 (the old cost): a
measurement past its age, a tool upgrade, GOH_PROVEN=0. Pinned by tests/test_prestep_dup.py.
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
import os
import subprocess
import sys
from pathlib import Path

MEASURES, MAY_REUSE = 0, 1
KEYED = ("inputs", "scripts", "runner")  # gate_identity.rust lines that list the files they key
ALSO = (".gatesrc", "diffcov_allow.json")


def _changed(root: Path, base: str) -> list[str] | None:
    r = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "--no-renames", base, "HEAD"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )  # fmt: skip
    return None if r.returncode else [p for p in r.stdout.splitlines() if p]


def keyed(lines: list[str]) -> set[str]:
    """The paths the Rust coverage identity's lines key (each after its kind and hash)."""
    out = set(ALSO)
    for line in lines:
        words = line.split()
        if words and words[0] in KEYED:
            out.update(words[2:])
    return out


def _hit(path: str, paths: set[str]) -> bool:
    return any(path == p or path.startswith(f"{p.rstrip('/')}/") for p in paths)


def decide(root: Path, base: str, external: str | None) -> tuple[int, str]:
    """(MEASURES|MAY_REUSE, why)."""
    if not external:
        return MAY_REUSE, "no instrumented suite (GOH_COV_RUST_EXTERNAL): the gate may reuse"
    changed = _changed(root, base)
    if changed is None:
        return MAY_REUSE, f"git cannot diff main {base[:8]}..HEAD"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import gate_identity  # lib/, beside this file
    finally:
        del sys.path[0]
    gates = root if (root / "lib" / "gate_identity.py").is_file() else gate_identity.GOH_ROOT
    try:
        lines = gate_identity.rust(root, gates, external)
    except LookupError as e:
        return MEASURES, f"the coverage always measures here ({e})"
    if moved := sorted(p for p in changed if _hit(p, keyed(lines))):
        more = f" (+{len(moved) - 1} more)" if len(moved) > 1 else ""
        return MEASURES, f"{moved[0]}{more} moved since main {base[:8]}: the gate measures coverage"
    return MAY_REUSE, f"no coverage input moved since main {base[:8]}: the gate may reuse the set"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--base", required=True)
    ap.add_argument("--external", help="GOH_COV_RUST_EXTERNAL as the gate has it")
    a = ap.parse_args(argv)
    code, why = decide(a.root.resolve(), a.base, a.external)
    print(why)
    return code


if __name__ == "__main__":
    sys.exit(main())
