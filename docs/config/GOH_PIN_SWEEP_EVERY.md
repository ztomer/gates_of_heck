# GOH_PIN_SWEEP_EVERY
Area: ship
Kind: setting
Order: 62
Default: `86400`

Seconds after the last pin sweep's pushes started at which the next one is due whatever follows it
(`lib/pin_schedule.py`, GOH.PINSWEEP): releases closer together than `GOH_PIN_SWEEP_QUIET` would
otherwise put it off for good. With no sweep run yet, it counts from the oldest one recorded.
