# GOH_SHIP_QUIET_IDLE_PCT
Area: ship
Kind: setting
Order: 80
Default: `30`

The measured CPU idle (%, `lib/capacity.py` tick counters) under which `goh ship` waits before
taking the land lock, so its suite does not measure the lanes beside it (GOH.SHIPQUIET,
`lib/ship_quiet.py`). The wait is recorded in the result's `quiet` (bound, seconds waited, first and
last idle, load1); every step entry carries `idle_pct` over its own run and `load1_end`. An idle
that cannot be read never holds a ship.
