# GOH_RUST_LINT_CARGO
Area: rust
Kind: setting
Order: 120
Default: `cargo`

The cargo command `GOH_RUST_LINT_CONFIGS` entries that name `--target` run through; host entries
keep `cargo`. `cargo-zigbuild` brings `zig cc`, which a target build script's C needs (ring for
musl, from macOS); it and `zig` are then required up front (`required_tools.py` layer `rust-cross`).
Any other value must be on PATH.
