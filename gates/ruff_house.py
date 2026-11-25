#!/usr/bin/env python3
"""ruff_house.py -- the ONE house ruff rule set, which every goh ruff step adds to a repo's own.

Before it (RUFF.RULES, 2026-11-11) the estate's ruff configs ran from `select = ["PLW1510"]`
(koffee_big, divoom-control, CadGoose, app_updates: a `ruff check` that reads as a lint gate and
checks one rule) to monitor's eighteen families, and nothing said which was the house's. The set
below is the floor: goh's ruff steps pass it as `--extend-select`, which is a later layer than the
repo's config, so a repo ADDS rules (its own `select`/`extend-select` stay in force) and cannot
subtract a house rule with `[lint] ignore` -- what it does not fix yet is DEBT, held per file in
`GOH_PY_LINT_CEILINGS` (gates/py_lint_ceiling.py), counted at the day it was seeded and only
allowed to fall. `per-file-ignores` still apply: they are a ruling about a path (tests may
`assert`), not a rule switched off.

The families are the ones that find defects or dead weight with almost no false positives:
pycodestyle errors/warnings, pyflakes, isort, pyupgrade, bugbear, blind-except, comprehensions,
simplify, pie, pylint errors and warnings (PLW1510, the G17b floor, is one of them) and ruff's own.
Wider families (S, PTH, PLR, TRY, FURB, DTZ, PERF, ANN, D) stay per-repo additions: across
the estate on 2026-11-11 they were 23 211 (S), 8 381 (PTH), 4 381 (PLR) and 1 791 (TRY) findings,
mostly style rulings a repo makes for itself. PERF was a candidate and is not: its 242 findings are
nearly all PERF401 ("use list.extend"), which ruff 0.17 cannot fix and which turns a two-line
loop into a generator argument -- a readability ruling, not a defect.

    ruff_house.py            # prints the set as ruff's comma list, for `--extend-select`
    ruff_house.py --pin      # prints RUFF, the one ruff release the house gates judge with
    ruff_house.py --bump-if-newer FOUND  # FOUND is not RUFF: start goh's bump-only land, detached

RUFF (GOH.RUFFVERSION, 2026-11-14): divoom_control was red on GitHub with SIM102 while its local
gate was green -- the local gate ran 0.17.0 and hosted CI installed the newest, 0.17.4. A ruff
release moves rules, so two rufs are two gates. The Python gates refuse any other ruff from a
runner that is machine state (gates/_py.sh goh_py_ruff_pinned), hosted CI installs exactly this
(.github/workflows/gate.yml GOH_CI_RUFF, the manifest's ruff rows), and tools/pytest.sh holds every
ruff on the machine to it (gates/ruff_agree.py --pin). The newest release, named; raise it in its
own commit, in gates/required_tools.tsv, the one place it is written (GOH.CLASS.PARITY).

The ONE house ruff rule set, passed as `--extend-select` by every goh ruff step under
`GOH_PY_LINT_CEILINGS` (RUFF.RULES). Invoked by via py_lint_ceiling, _py.sh.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    # A copy outside any checkout has nothing to re-run from.
    with _contextlib.suppress(ModuleNotFoundError):
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import os
import sys


def _pin() -> str:
    """The manifest's ruff pin (gates/required_tools.tsv `=X.Y.Z`): the version is written there
    and nowhere else (GOH.CLASS.PARITY, tests/test_tool_version_manifest.py)."""
    manifest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "required_tools.tsv")
    with open(manifest, encoding="utf-8") as fh:
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            ruff_row = not line.startswith("#") and len(cols) == 6 and cols[1] == "ruff"
            if ruff_row and cols[5].startswith("="):
                return cols[5][1:]
    raise SystemExit(f"ruff_house.py: {manifest} pins no ruff (a `=X.Y.Z` floor on a ruff row)")


RUFF = _pin()

HOUSE: tuple[str, ...] = (
    "B",  # flake8-bugbear
    "BLE",  # blind except
    "C4",  # comprehensions
    "E",  # pycodestyle errors
    "F",  # pyflakes
    "I",  # isort
    "PIE",  # flake8-pie
    "PLE",  # pylint errors
    "PLW",  # pylint warnings (PLW1510: a subprocess.run says what a failing child does)
    "RUF",  # ruff's own
    "SIM",  # flake8-simplify
    "UP",  # pyupgrade
    "W",  # pycodestyle warnings
)


def extend_select() -> str:
    """The set as ruff's `--extend-select` value."""
    return ",".join(HOUSE)


# THE REPO'S OWN CONFIG CARRIES THE SET from this day (GOH.RUFFHOUSESET): an unset or `warn`
# GOH_RUFF_CONFIG_PARITY reads as `on`, and a repo with no ceilings whose files the set flags is
# refused. The gate cannot fall behind (it passes the set itself); the repo's copy, which a plain
# `ruff check` reads, can, so it is judged. A vended base config is not by construction (probed
# 2026-11-23, ruff 0.17.6): a repo's own `select` replaces a base's `extend-select`, and an
# `extend = "${GOH_DIR}/..."` fails ruff outright wherever GOH_DIR is unset.
OVER_DAY = "2026-12-07"
OVER_FROM = 1_796_601_600  # 2026-12-07T00:00:00Z


def over(now: float | None = None) -> bool:
    """Is the grace over? `GOH_GRACE` over/within decides, as crate::grace's seam; else the day."""
    seam = os.environ.get("GOH_GRACE")
    if seam in ("over", "within"):
        return seam == "over"
    import time

    return (time.time() if now is None else now) >= OVER_FROM


def bump_if_newer(found: str) -> None:
    """A gate met ruff FOUND, not the pin: ask goh's binary for `goh land --ruff-bump --detach`
    (lib/ruff_bump.py, GOH.RUFFBUMPLAND), which lands the bump once when FOUND is the box's newer
    release. A side effect of the red, no input of its verdict: never waited for (the binary
    detaches a session of its own, holding no lock of this gate's), its failure never the gate's.
    Through the binary, not the script, so no gate's key or lock closure grows by the lander."""
    goh = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "goh")
    if not found or found == RUFF or not os.access(goh, os.X_OK):
        return
    import subprocess

    try:
        subprocess.run([goh, "land", "--ruff-bump", "--detach"], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=60, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return


if __name__ == "__main__" and sys.argv[1:2] == ["--bump-if-newer"]:
    bump_if_newer((sys.argv[2:3] or [""])[0])
    sys.exit(0)
if __name__ == "__main__" and sys.argv[1:] == ["--over"]:
    print(f"over {OVER_DAY}" if over() else f"within {OVER_DAY}")
    sys.exit(0)
if __name__ == "__main__":
    print(RUFF if sys.argv[1:] == ["--pin"] else extend_select())
    sys.exit(0)
