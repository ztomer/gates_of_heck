# GOH_LAND_TIMINGS_KEEP
Area: pushes
Kind: internal
Order: 240
Default: unset

Not a setting: where the lander has `tools/land.sh` keep its gate timings at exit, beside the land's
log in the queue's `logs/` (`<log stem>.timings.jsonl`; `lib/land_prestep.py timings_keep`): the
spool of the land's rows in goh-cache.db's `steps`, gone once they are there (`lib/steps_store.py`,
GOH.STEPTIMES); `goh land-profile` reads the full step list by that name. The `$TMPDIR` copy and the
gate logs are removed (GOH.LANDLEAKS).
