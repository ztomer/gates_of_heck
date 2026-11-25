# GOH_LAND_VERDICTS_CACHE
Area: pushes
Kind: internal
Order: 290
Default: unset

Not a setting: the goh-cache.db holding the caller's verdict records (`lib/reuse_store.py`:
`GOH_CACHE_DB`, else beside a named `GOH_REUSE_DIR`, else `$XDG_CACHE_HOME/goh/goh-cache.db`),
resolved by `tests/suite_home.py` beside `GOH_LAND_VERDICTS_STORE`; the controller opens it for the
call only (`reuse_store.NAMED`), the one real-home goh-cache.db a suite may write.
