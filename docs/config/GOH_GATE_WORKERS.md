# GOH_GATE_WORKERS
Area: pushes
Kind: internal
Order: 590
Default: set by the admission

This gate's share of the cores (GOH.POOLSHARE, `lib/worker_share.py`): cores / the gates admitted
now, this one included, either lane, at least 1 -- exported at admission (`lib/gate_slot.sh`,
`lib/admission.py`) with every runner's own variable beside it (`PYTEST_XDIST_AUTO_NUM_WORKERS`,
`CARGO_BUILD_JOBS`, `RUST_TEST_THREADS`, `NEXTEST_TEST_THREADS`; one already set lower stays), and
passed as a flag where a runner reads none (`swift --jobs`, `xcodebuild -jobs` /
`-maximum-parallel-testing-workers`, the probe's `pytest -n`). `local_ci.sh` gives each of
`GOH_CI_JOBS` steps at once 1/N of it; `tools/pytest.sh` under no slot (a land's pre-step) counts
itself as one more gate. Never MAKEFLAGS: make is serial unset, and a count would widen it -- a gate
script says `make -j"$(goh_workers)"`. 2026-11-17: five gates each sized to all 16 cores put ~220
threads on them (load 213-237). Not a setting.
