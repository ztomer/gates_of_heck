# GOH_RUST_TEST_UNREAD
Area: rust
Kind: setting
Order: 110
Default: unset

Git globs (space-separated) no Rust test of the repo reads: a land's `cargo test (plain)`
(`GOH_RUST_COVERAGE=plain`) is keyed on the tree minus these (`gates/_rust_inputs.sh` mode `tests`,
GOH.GATEFLOOR), so a change confined to them skips it. Unset keys the whole tree. A claim about
every Rust test: goh holds its own value to it (`tests/test_rust_gate_floor.py`: no Rust source
reads a path it names). This repo: the Python suite, docs and changelog fragments.
