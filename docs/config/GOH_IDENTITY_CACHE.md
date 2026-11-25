# GOH_IDENTITY_CACHE
Area: pushes
Kind: setting
Order: 570
Default: unset (goh-cache.db's own)

A directory whose `goh-cache.db` holds `lib/identity_cache.py`'s memo instead of goh-cache.db's own
(`GOH_CACHE_DB` wins): `lib/gate_identity.py`'s `git ls-files` listing and `goh_version.sh --inputs`
answer across processes (GOH.IDENTITYPORT), each keyed on everything it depends on, in the `memo`
table (GOH.VERDICTDB), at most 512 rows per kind, oldest dropped. Deleting it is always safe.
