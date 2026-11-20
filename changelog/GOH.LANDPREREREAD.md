- **land.sh: the pre-step is the one the rebased tree declares** (GOH.LANDPREREREAD). `GOH_LAND_PRE` and
  `GOH_SHIP_FULL_SUITE` were read from `.gatesrc` once, before the rebase onto main, so a main that changed the
  declaration had its new tools run with the lane's old command: GOH.CADOVERRIDE's land (2026-11-19) ran
  `affected_tests.py --fast-only` after GOH.COVOFFLAND removed the flag, and landed nothing. Both are read again after
  every rebase (`tests/test_land_prestep_rebase.py`).
