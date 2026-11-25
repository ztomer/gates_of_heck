# GOH_SCCACHE_PINNED
Area: rust
Kind: setting
Order: 20
Default: set by `lib/sccache_dir.sh`

The `SCCACHE_DIR` goh pinned, which is a fallback and not the user's choice: with it (or the per-OS
default, or none) `lib/goh-rustc` starts a missing server on the resolver's root; any other
`SCCACHE_DIR`, a `SCCACHE_SERVER_PORT` or a `SCCACHE_SERVER_UDS` is a private server and left alone.
