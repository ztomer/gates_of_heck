# GOH_SHIP_FULL_SUITE
Area: pushes
Kind: setting
Order: 360
Default: unset (goh: `on`)

`on`: `tools/release_goh.sh` refuses, before anything moves, a tip whose tree no FULL suite run
judged under today's tools (`lib/reuse.py full-suite`): a whole-suite run that failed nothing and
carried nothing from another tree. A land that carried is green but no full run; `goh ship`'s
`suite` step makes the instrumented full run on every ship (`lib/ship_full_run.py`), judging every
coverage floor over what landed since the last ship, so a land of such a repo measures no coverage
(`GOH_RUST_COVERAGE=plain`, GOH.COVOFFLAND). Pinned by `test_verdict_carry.py`,
`test_ship_cov_bisect.py`.
