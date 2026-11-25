# GOH_BENCH_LOCK_DIR
Area: pushes
Kind: setting
Order: 700
Default: `/tmp/gates-of-heck-bench.lock`

The host-wide bench lock (`lib/bench_lock.sh`): every gate registers here (its stage and repo) and a
measurement (`tools/quiet.sh`) holds it exclusive; `tools/load.py` reads `gates/`
(`lib/gates_in_flight.py`) and refuses to starve a live one. Machine-wide on purpose; set it only to
isolate a test.
