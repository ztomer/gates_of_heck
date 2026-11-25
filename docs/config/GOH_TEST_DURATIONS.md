# GOH_TEST_DURATIONS
Area: python
Kind: internal
Order: 70
Default: set by `tools/pytest.sh`: goh.db (`GOH_DB`, else `$XDG_STATE_HOME/goh/goh.db`; `lib/fast_tests.py` `cache()`)

The database holding the suite's duration table (GOH.DURATIONSDB, `lib/durations.py`: one row per
reading, the `durations` view per pytest node id): the longest-first order (`tests/_schedule.py`)
and the land pre-step's fast judges (`lib/fast_tests.py`) read it. `tools/pytest.sh` sets it for a
TOP-LEVEL run only and `tests/suite_home.py` pops it before anything starts a child, so that run
records its readings (one transaction, its prunes with them) and no xdist worker or fixture run
under it writes (GOH.DURATIONSSTALE); a run nested on purpose (`GATES_OF_HECK_SUITE_NESTED_OK`) has
it unset. It orders tests and chooses the pre-step's first judges, never a verdict. Not a setting.
