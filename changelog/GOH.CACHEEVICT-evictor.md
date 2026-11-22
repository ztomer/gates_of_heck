* `goh cache-sweep plan|run` (`lib/cache_evict.py`, `lib/cache_sweep.py`, `lib/cache_move.py`,
  GOH.CACHEEVICT): one value-aware evictor ranks the cargo build dirs, the llvm-cov entries and the
  sccache bytes together (GDSF: the clock at last use plus decayed uses x rebuild seconds / bytes),
  never touching an object a live build holds. With the removable tier present the internal disk
  keeps a 60 GiB hot set and demotes the rest to the drive instead of deleting it; the rust gate
  promotes a demoted build dir back before cargo reads it.
