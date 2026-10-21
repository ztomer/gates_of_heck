"""The busy-loop cases of the shared-gate canary, split out of `_canary_cases.py`.

WHY A SPLIT, and why this file. The table in `_canary_cases.py` is the house's list of the gates
that can fail, one case each, and it had reached the 500-line cap -- a hard rule ("split, never
exempt"). The busy-loop cases are their own group (the gate that refuses a tracked shell file
running a backgrounded busy loop, `goh busy-loop`), so they live here rather than pushing the
table over.

THE LIST IS STILL ONE LIST. `_canary_cases.py` splices these in, so every reader -- this repo's
`tests/test_canary.py` and game_asset_factory's `tools/check_gate_canary.py` -- sees one table and
does not know a file was split. A case is (label, gate script, argv, violating tree, clean tree),
and it is worthless unless BOTH directions hold.
"""

# (label, script, argv, {path: violating content}, {path: clean content})
CASES = [
    (
        "a tracked shell file that runs a backgrounded busy loop is refused",
        "check_busy_loop.py",
        [],
        {"load.sh": "#!/bin/sh\nfor i in $(seq 1 24); do (while :; do :; done) & done\n"},
        {"load.sh": "#!/bin/sh\necho 'while :; do :; done'\n"},
    ),
]
