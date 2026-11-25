# GOH_CACHE_DB_BUSY_MS
Area: pushes
Kind: setting
Order: 350
Default: `2000`

Milliseconds a goh-cache.db open or write waits on another writer; past it the lookup is a miss or
the write is skipped.
