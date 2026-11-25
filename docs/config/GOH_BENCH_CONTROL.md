# GOH_BENCH_CONTROL
Area: pushes
Kind: setting
Order: 750
Default: 200 spawns of `/usr/bin/true`, timed

A command printing seconds that `tools/quiet.sh` runs before and after each measurement; a run whose
control moves > 10% is noisy and fails. Tests only (a steady fake). The real control read 0.31 s on
this box with every gate drained (2026-10-08).
