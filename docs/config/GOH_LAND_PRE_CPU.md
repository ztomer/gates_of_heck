# GOH_LAND_PRE_CPU
Area: pushes
Kind: setting
Order: 200
Default: unset

The file the pre-step's pytest runs add their tree's CPU seconds to (`lib/pre_cpu.py`):
`tools/affected_tests.py` after each run it reaped, `lib/pre_deadline.py` from the live tree before
a deadline kill; `tools/land.sh` reads it as `pre_run.cpu_s` (GOH.PREWIDTH). Internal: land.sh sets
it beside the pre-step's start mark.
