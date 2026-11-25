# GOH_LAND_VERDICTS_STORE
Area: pushes
Kind: internal
Order: 280
Default: unset

Not a setting: the verdict store root of the caller of a goh suite run under
`GOH_LAND_VERDICTS_RECORD` or `_REUSE`, resolved by `tests/suite_home.py` before the suite replaces
HOME (`GOH_REUSE_DIR`, else `$XDG_STATE_HOME/goh/reuse`, else `~/.local/state/goh/reuse`), so the
land's `carry` records reach the real store; a fixture session names none and records nothing.
