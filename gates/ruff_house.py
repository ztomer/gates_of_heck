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

import sys

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


if __name__ == "__main__":
    print(extend_select())
    sys.exit(0)
