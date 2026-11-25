# GOH_POOL_DB
Area: pushes
Kind: setting
Order: 314
Default: config.toml's `pool_db`, else `$XDG_STATE_HOME/token-pool/pool.db` (`~/.local/state`)

Where the pool's spend database is (`lib/state_paths.py` `pool_db`, GOH.OLDFORMS): absolute or
`~/..`; anything else is refused by name. goh writes nothing there; the pool and the loop's backup
read the same key.
