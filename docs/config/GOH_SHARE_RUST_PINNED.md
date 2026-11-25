# GOH_SHARE_RUST_PINNED
Area: pushes
Kind: internal
Order: 690
Default: set by the admission

`1` when `RUST_TEST_THREADS` is the repo's own count (lower than or equal to the share, not
inherited): the live runner never re-sizes it. Not a setting.
