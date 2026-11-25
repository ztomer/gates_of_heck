# GOH_CACHE_EVICT_STATE
Area: cache-tiers
Kind: setting
Order: 40
Default: `~/.local/state/goh/cache-evict.json`

The evictor's ledger: uses, last use and clock per object, keyed by path, and the clock L. Its
record is goh.db's `kv` row `cache-evict` in the goh.db beside it (`GOH_DB` when set;
GOH.STATEDBS3); the file is read only until its import. Point it elsewhere only to isolate a test.
