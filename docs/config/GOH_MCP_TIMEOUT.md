# GOH_MCP_TIMEOUT
Area: local-ci
Kind: setting
Order: 50
Default: `300`

Seconds each `GOH_MCP_SERVERS` server gets, from its start to its last `tools/list` page: the wire
answer only. A server named as one `cargo [+toolchain] run ... [-- ARGS]` is built first by the same
words with `run` made `build` and ARGS dropped, outside this bound (GOH.MCPWIRELOAD: a release build
under load ate token-pool's 300 s). Bounded on purpose: a silent server is red at the deadline, and
its whole process group is reaped. Not a positive whole number: exit 2 naming this key.
