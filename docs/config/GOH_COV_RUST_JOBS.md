# GOH_COV_RUST_JOBS
Area: gate-source
Kind: setting
Order: 70
Default: `6`

rust coverage: how many of the workspace's instrumented test binaries run at once
(`gates/_cov_run_tests.py`, GOH.COVPARBINS). `cargo test --workspace` ran them one after another,
each parallel only inside itself; monitor's 82 binaries were 197 s of one-at-a-time with the other
cores idle. `1` is the old serial run (a test that shares a fixed port or file with another binary
needs it). How fast, never what is measured: each process writes its own profile, and the merged
report is the same.
