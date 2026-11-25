# GOH_FSEVENTSD_RSS_KB, GOH_FSEVENTSD_LIMIT_KB
Area: verdict-store
Kind: setting
Order: 20
Default: the daemon's own RSS (`ps`); `1048576` (1 GB)

`lib/fseventsd_health.py` (GOH.FSEVCHURN), asked after a whole suite: the RSS reading (a number, or
`none` for no daemon -- a test seam) and the bound over which a saturated fseventsd is named with
its restart. Not consumer config.
