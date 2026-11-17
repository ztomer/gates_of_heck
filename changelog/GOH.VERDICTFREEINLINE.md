* A GOH_* key passed inline or by `env` can no longer reach a proven step's key unseen
  (GOH.VERDICTFREEINLINE). `tests/test_verdict_free_keys.py` found only `export GOH_X`, so a value
  set the way `gates/push_gate.sh` sets GOH_STRUCTURAL_MARK (`GOH_X=v cmd`), by `env GOH_X=v cmd`, or
  by Python's `dict(os.environ, ...)` / `os.environ[...] =` was invisible, and every key of that
  class hid GOH.RUSTCOVREUSE. The test now reads every form, each key passed on is on
  `gates/verdict_free_keys.txt` or in a reasoned exemption (a stale exemption fails), and a fixture
  with an inline per-push key goes red. It found real gaps, now on the list: GOH_STAGED_TWIN_MARK
  (a new temp file per commit), GOH_CHANGE_BASE (a new commit per push) and the four GOH_SHIP_* keys
  (new per ship); `GOH_SHIP_SUMMARY` and `GOH_SHIP_ITEM` gained their docs/config.md rows.
