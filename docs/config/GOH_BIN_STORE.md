# GOH_BIN_STORE
Area: python
Kind: internal
Order: 30
Default: `~/.cache/goh/by-stamp`

Not a setting: a LOCATION, the machine-wide store of goh builds keyed by source stamp
(`gates/_goh_bin.sh`, G2e). Every gates root reads it before building and every build lands in it
too, so one source is built once per machine, not once per worktree, export or clone. A binary is
taken only if it reports the stamp it is filed under. A test of the store names a private one.
