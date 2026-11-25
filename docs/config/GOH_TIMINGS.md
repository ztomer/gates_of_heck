# GOH_TIMINGS
Area: runtime
Kind: setting
Order: 150
Default: unset

A file path. Every bounded step of both structural tiers, every `goh_step`, and every `local_ci.sh`
step (and its proven-cache hits, marked `"cache": "hit"`) appends ONE JSON line: `label`, `ms`,
`rc`, `tier`, `parent`, `cwd` (`lib/step_timings.py`), and for a wrapped step (`goh step`,
`lib/bounded_run.py`) `cpu_ms`: the user + system CPU of the tree it reaped, the step's own work on
a box whose load also sets `ms`. Whole lines under `O_APPEND`, so concurrent gates share one file. A
failed write is dropped: the instrument never changes a verdict. Steps run with `GOH_STEP_TIMEOUT=0`
(unbounded) are not timed. Report: `python3 lib/step_timings.py report FILE`; front end:
`tools/gate_profile.sh <repo>`.
