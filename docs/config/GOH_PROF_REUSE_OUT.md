# GOH_PROF_REUSE_OUT
Area: pushes
Kind: internal
Order: 270
Default: unset

Not a setting: the file a ship's instrumented run writes its reused and fresh binary tests to, per
binary (`tests/_prof_reuse.py`, GOH.COVPROFILEREUSE), named by `lib/ship_full_run.py --profiles-out`
and folded into the ship record's `suite` step (`profiles`) by `tools/ship.py`; taken out of the
environment as the plugin loads, so no session a test starts writes it.
