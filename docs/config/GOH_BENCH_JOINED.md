# GOH_BENCH_JOINED
Area: runtime
Kind: internal
Order: 250
Default: set by `lib/bench_lock.sh`

The pid of the gate that registered this process tree with the bench lock, so its children do not
register again. A pid per run: in a cache key it would re-gate everything, so it is verdict-free.
Not a setting.
