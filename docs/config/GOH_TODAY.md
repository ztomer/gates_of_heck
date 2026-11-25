# GOH_TODAY
Area: ship
Kind: setting
Order: 30
Default: unset

The day `tools/release-kit/changelog_cut.py` stamps a cut heading with, `YYYY-MM-DD`; unset is the
wall-clock day, malformed is an error (GOH.MIDNIGHTDATE). The clock seam the suite pins:
`tests/suite_home.py` `CLOCK` (carried by `hermetic_env`) sets it to `PINNED_TODAY`, so a gate that
crosses midnight cannot split the day a test expects from the day the code writes, and
`tests/test_no_wallclock_expected.py` refuses a test that builds an expected date from the clock.
