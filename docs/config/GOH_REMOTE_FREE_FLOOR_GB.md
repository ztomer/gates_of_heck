# GOH_REMOTE_FREE_FLOOR_GB
Area: remote-worker
Kind: setting
Order: 110
Default: `100`

The free space the host's scratch disk keeps for its transcodes: below it the shard runs locally,
saying why; after every run `lib/worker_evict.py` evicts the worker's caches coldest first back
above it.
