# GOH_SHARE_STEPS
Area: pushes
Kind: internal
Order: 660
Default: set by `local_ci.sh` and `rust_gate.sh`

`:`-joined files, each one `local_ci.sh` scheduler's count of steps running NOW (`GOH_CI_JOBS` > 1);
each divides the live share, so a step grows as its siblings finish. A file may say `K/N` instead, this run's part by name: a
land's `cargo test (plain)` has `1/2` beside the external suite (`gates/_rust_coverage.sh`) until the
suite ends and the file goes. Not a setting.
