# GOH_SHIP_TIMEOUT
Area: ship
Kind: setting
Order: 70
Default: `3600`

Seconds each step may run before its whole process group is TERM'd then KILL'd
(`lib/bounded_run.py`); the step is `timed_out` in the result.
