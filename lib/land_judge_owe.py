"""lib/land_judge_owe.py -- the row a goh land owes main's judge, and the judging started
(GOH.ONEGATE).

    land_judge_owe.py --main M --base B --tip T --rc RC --changed 0|1 --root R [--timings F]

By tools/land.sh at its end, the land lock still held: a landed tip's row, and a red gate's that
named a step (the steps its own gate had red, from its step rows). A lane that changed gate code is
`waiting`, and the drain (lib/land_judge.py) is started in a session of its own, which holds none of
the land's locks and is never waited on; one that changed none is `same`. Takes no lock, so the lock
order's graph has no edge from a land to the judge's. Inside a goh suite no drain is started.
Pinned by tests/test_land_judge.py and tests/test_land_driver.py.

GOH.ONEGATE: the row a goh land owes main's judge (`land_judge_rows.py`), and the detached drain
(`land_judge.py`). Invoked by `land_gate.sh`.
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
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "lib"), str(ROOT)]
import full_run  # noqa: E402 -- lib/ and the root, on the path from the line above
import land_judge_rows as rows  # noqa: E402
import land_judge_steps as steps  # noqa: E402
import span_store  # noqa: E402
import state_paths  # noqa: E402

from tui.lib import err, info  # noqa: E402


def red_steps(timings: str | None, listed: list[str]) -> dict[str, int]:
    """{step: its exit} of the LISTED steps the land's own gate had red, from its step rows."""
    last: dict[str, int] = {}
    try:
        lines = Path(timings).read_text(errors="replace").splitlines() if timings else []
    except OSError:
        return {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        for step in listed:
            if isinstance(row, dict) and row.get("label") == step[:300]:
                last[step] = int(row.get("rc") or 0)
    return {s: rc for s, rc in last.items() if rc}


def owe(a: argparse.Namespace) -> int:
    """The land's row; a drain started for an owed one. A red land with no red step has no row."""
    red = {}
    if a.rc:
        red = red_steps(a.timings, steps.declared(a.root)[0])
        if not red:
            return 0
    rows.owe((a.repo, a.tip, a.base), a.rc, bool(a.changed), red, time.time())
    if not a.changed:
        return 0
    info(f"judge: main {a.base[:8]}'s code judges what {a.tip[:8]} changed, after this land "
         "(land_judge.py report)")  # fmt: skip
    if any(os.environ.get(k) for k in full_run.IN_SUITE):
        return 0
    home = state_paths.landq_dir() / "judge"
    home.mkdir(parents=True, exist_ok=True)
    with (home / "judge.log").open("a") as out:
        env = {k: v for k, v in os.environ.items() if k != "GOH_HELD_LOCKS"}  # holds none of them
        subprocess.Popen([sys.executable, str(ROOT / "lib" / "land_judge.py"), "drain", "--main",
                          str(a.main)], stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True, env=env)  # fmt: skip
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--main", type=Path, required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--tip", required=True)
    ap.add_argument("--rc", type=int, required=True)
    ap.add_argument("--changed", type=int, required=True)
    ap.add_argument("--root", type=Path, required=True, help="main's export: its list names steps")
    ap.add_argument("--timings", help="the land's step rows")
    a = ap.parse_args(argv)
    a.repo = span_store.key(a.main.name)
    try:
        return owe(a)
    except Exception as e:  # noqa: BLE001 -- said, and the land says it has no row
        err(f"land judge: unavailable: {e.__class__.__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
