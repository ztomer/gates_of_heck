* `goh sccache-repoint check|apply` (`lib/sccache_repoint.py`, GOH.CACHEEVICT): the sccache server
  follows the removable drive. When its cache location differs from the resolver's root, it waits
  for sccache to go idle (no new compile requests for 30 s, no rustc running), stops the server and
  starts it on the new root from cwd `/`, never with SCCACHE_BASEDIRS or SCCACHE_CACHE_SIZE.
