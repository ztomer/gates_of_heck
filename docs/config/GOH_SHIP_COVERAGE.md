# GOH_SHIP_COVERAGE
Area: pushes
Kind: setting
Order: 370
Default: unset

`dummy` (one line in `.gatesrc`, owner 2026-11-19): coverage is measured nowhere -- a land runs its
tests plain (GOH.COVOFFLAND) and no ship measures -- so `goh ship` refuses before anything starts,
"coverage in dummy mode; no release" (exit 2). Any other value is refused. Turning coverage back on
is one step: delete that line; the next ship's `suite` step measures every floor over what landed
since the last ship. Pinned by `test_ship_cov_bisect.py`.
