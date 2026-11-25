# GOH_LAND_TIMINGS_KEEP
Area: pushes
Kind: internal
Order: 240
Default: unset

Not a setting: where the lander has `tools/land.sh` keep its gate timings at exit, beside the land's
log in the queue's `logs/` (`<log stem>.timings.jsonl`, pruned with the logs; `lib/land_prestep.py
timings_keep`); `goh land-profile` reads the full step list from it. The `$TMPDIR` copy and the gate
logs are removed (GOH.LANDLEAKS).
