# GOH_COV_RUST_EXTERNAL
Area: coverage
Kind: setting
Order: 40
Default: unset

rust only (`--external`): a command run against the INSTRUMENTED binaries, its profiles counted as
one more part -- for a crate whose behaviour suite drives its binary from outside `cargo test`. The
command runs in the CALLER's environment (never the instrumented build's) plus `LLVM_PROFILE_FILE`
and `GOH_COVERAGE_BIN_DIR` (the instrumented `target/.../debug`); a failing command is a missing
part, never a smaller report. `rust_gate.sh` then keys its coverage group on the whole tree. This
repo: the pytest suite, with `GOH_TEST_BIN` naming the instrumented `goh` (62.5% -> counted).
