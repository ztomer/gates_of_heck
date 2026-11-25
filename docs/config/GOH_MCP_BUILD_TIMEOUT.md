# GOH_MCP_BUILD_TIMEOUT
Area: local-ci
Kind: setting
Order: 60
Default: `1800`

Seconds the derived `cargo build` of a `GOH_MCP_SERVERS` server gets, before the server starts. Past
it, or a non-zero exit, is red as `build:` with the build's output tail, its process group reaped,
and no server started. The build and the server run with `CARGO_HOME`/`RUSTUP_HOME` pinned to the
owner's (when the directories exist and none is set), so a server that moves `HOME` into its scratch
does not have rustup fetch a toolchain into it. Not a positive whole number: exit 2 naming this key.
