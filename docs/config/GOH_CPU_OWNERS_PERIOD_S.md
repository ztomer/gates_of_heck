# GOH_CPU_OWNERS_PERIOD_S
Area: runtime
Kind: setting
Order: 404
Default: 120

env, `lib/cpu_owners.py`: how often the load sampler's loop (`lib/load_sources.py sample`) books a window of CPU by owner into goh.db `cpu_owners`; the window's own cost is its `sampler` row. A value that is no number is the default.
