# GOH_CRATES_IO_CACHE
Area: deps
Kind: setting
Order: 220
Default: `~/.cache/goh/crates-io`

Where crates.io answers are kept, one file per crate name, shared by every crate and repo; `off`,
`0`, `false` or `no` asks every time, and so does a relative path (`crates/goh/src/cache_root.rs`).
Read only by the report-only arm; an UNREACHED lookup is never cached.
