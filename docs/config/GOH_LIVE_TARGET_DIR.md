# GOH_LIVE_TARGET_DIR
Area: gate-source
Kind: setting
Order: 90
Default: `<checkout>/target/goh-live`

Under `GOH_LIVE`, when the working tree's Rust (crates/, Cargo.toml, Cargo.lock,
rust-toolchain.toml) differs from HEAD, `_goh_bin.sh` builds THAT `goh` here, once per (HEAD Rust
stamp, diff) pair, and runs it -- a moved HEAD is a rebuild, never the old binary -- `bin/goh` is
HEAD's (C3) and would answer for code the run is not testing.
