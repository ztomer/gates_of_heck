# GOH_LAND_PRE_FROM
Area: pushes
Kind: setting
Order: 170
Default: unset

The kept enqueue verdict (`GOH_LAND_PRE_KEEP`'s file) the lander hands `tools/land.sh`: a rebased
tip whose diff from the judged sha moves only paths no test reads (`lib/pre_reuse.py`,
`tools/affected_tests.py` `readers`) is not pre-stepped again, one some tests read runs those alone,
anything else runs the set. Missing or unreadable is no verdict: the set runs (GOH.PRELANDTWICE).
Internal: the queue sets it.
