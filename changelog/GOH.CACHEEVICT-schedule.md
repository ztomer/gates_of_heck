* The cache evictor runs on its own schedule and owns the backstops (GOH.CACHEEVICT): the launchd job
  `com.ztomer.goh-cache-sweep` (`tools/install_cache_sweep.sh install`) runs `goh cache-sweep run`
  every 30 min; the coverage run promotes a demoted llvm-cov entry before its hit check; sccache's
  cap is the evictor's `SCCACHE_GIB` (still 40 GiB), so sccache's own LRU is the backstop at the
  evictor's number. Only cargo-shaped `<hex>/<hex>` build dirs are evictor objects.
