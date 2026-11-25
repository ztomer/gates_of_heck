# GOH_NO_HOME_PATHS
Area: structural
Kind: setting
Order: 190
Default: unset (check skipped)

Set to `1` to run `goh home-paths`: no `/Users/<x>/…`, `/home/<x>/…`, `~/Projects/…` or
`$HOME/Projects/…` in any tracked text file (a `${VAR:-default}` expansion is derived, not
hard-coded, and passes). A path that must stand carries `path-ok: <reason>` on the line or above.
Opt-in per repo — turn it on once the tree is clean, never red in twenty places at once. Same
`GOH_EXCLUDE`.
