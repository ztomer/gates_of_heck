# GOH_COV_CACHE_MAX_GIB
Area: gate-source
Kind: setting
Order: 60
Default: `40`

The byte budget of `GOH_COV_CACHE`, sized from the estate's measured working set (GOH.COVCACHECHURN:
at `8`, least-recently-used eviction against a push sweep that cycles through more than the budget
evicted every build just before its repo's next push, and every push built cold;
`gates/_cov_cache.py`, the measured table in `tests/test_cov_cache_budget.py`). It never grows into
the lanes' disk floor: it shrinks to the volume's free space minus `GOH_LANE_MIN_FREE_GIB` and 10
GiB for the build in flight. An entry whose repo root (`.goh-root`) is gone, and no push export's
`.source` names, goes first, whatever the budget. Each run appends a row to `.runs.jsonl` (hit = its
build was still there); `python3 gates/_cov_cache.py report` prints the hit rate. Past the budget,
the least recently used builds not in use go until it fits (`gates/_head_cache.py`), at the start of
each run, after each of its instrumented builds (while it is live), and when it exits, pass or fail.
A count bounds entries, not bytes: 334 builds inside their week held 11 GiB; and "idle for an hour"
is no bound in a sweep (40 builds in one hour held 21 GiB, GOH.CACHEBOUND), so a run records its pid
in `.goh-running` and liveness decides. `0` turns the budget off.
