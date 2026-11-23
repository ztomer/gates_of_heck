* `goh remote-shard` ends every remote run with goh's cache evictor inside the worker
  (`lib/worker_evict.py`, the GOH.CACHEEVICT ranking): the worker dir stays under its 80 GB
  ceiling and the scratch disk keeps its 100 GB free floor, coldest object first, never the slot
  that just ran; it reports and never changes the run's verdict. The worker image carries sccache
  0.19.0 (checksummed) behind goh's rustc wrapper: a second cold build of goh on the worker took
  16 s against 32 s, 167 of 167 cacheable compiles hit.
