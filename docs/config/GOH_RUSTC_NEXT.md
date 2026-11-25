# GOH_RUSTC_NEXT
Area: rust
Kind: setting
Order: 60
Default: `sccache` (on PATH)

What `lib/goh-rustc` runs after it drops the variables only cargo reads (`CARGO_TARGET_DIR`,
`CARGO_HOME`, `CARGO_INCREMENTAL`, `CARGO_BUILD_*`, `CARGO_TERM_*`) from a rustc invocation's
environment, which sccache 0.19 would hash into the key (GOH.SCCACHEENV, F1b). A SEAM read from the
environment: a test names a recorder or a private sccache.
