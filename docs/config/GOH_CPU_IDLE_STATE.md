# GOH_CPU_IDLE_STATE
Area: claude-hooks
Kind: setting
Order: 70
Default: unset: goh-cache.db's own path

Names the CPU idle window's state for a test: the window is goh-cache.db's `kv` row `cpu_idle`, in
the goh-cache.db beside this name (`GOH_CACHE_DB` still wins; GOH.STATEDBS3). Every capacity reading
records its tick sample there, kept warm by one sampler that exits 900 s after the last reader.
