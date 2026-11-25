# GOH_LAND_TIMEOUT
Area: ship
Kind: setting
Order: 150
Default: `3600` (`7200` for a `--land` request)

Seconds the lander lets one request (its push gate, its `goh ship --wait`, or its worktree's
`tools/land.sh`) run before its whole process group is TERM'd then KILL'd; the request is recorded
`red`, `timed out`.
