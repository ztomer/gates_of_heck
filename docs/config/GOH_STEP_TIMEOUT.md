# GOH_STEP_TIMEOUT
Area: structural
Kind: setting
Order: 90
Default: **1800**

The wall-clock CEILING on every `goh_step` in every toolchain gate (`structural.sh`, `rust_gate.sh`,
`py_gate.sh`, `swift_gate.sh`) and on every step the native `bin/goh` delegates. Printed on each
step line; `0` opts out and says `UNBOUNDED` rather than looking identical to a bounded step;
non-numeric is exit 2, because a typo'd ceiling must not silently leave the step unbounded.
`goh_step` had **no** ceiling while `local_ci.sh` did, so one hang had two answers; the native tier
had none either and now routes every delegated step through `lib/bounded_run.py`, so all three paths
share one implementation of the bound. Judged via `--stall` (progress = advancing process-group CPU
or runnable threads) and `--cpu-timeout`, with a wall-clock backstop.
