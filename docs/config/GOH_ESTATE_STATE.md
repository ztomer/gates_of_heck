# GOH_ESTATE_STATE
Area: deps
Kind: setting
Order: 90
Default: `~/.local/state/goh/estate_audit.json`

env, `scripts/estate_audit.py` and `hooks/claude/estate_advisories.py`: names the last estate
audit's record, goh.db's `kv` row `estate_audit` in the goh.db beside it (`GOH_DB` when set;
`lib/small_stores.py`, GOH.STATEDBS3), with the pin push ledger (`pin_push`); the file itself is read only until its import. `XDG_STATE_HOME` moves the default.
Its DIRECTORY holds the estate registry (`installed_repos`), one line per repo `install.sh`
installed into; a registered path that is gone is named, never skipped.
