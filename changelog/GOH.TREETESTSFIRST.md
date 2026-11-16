* **A land runs the tests its diff chooses first, before the land lock and the gate (GOH.TREETESTSFIRST).**
  Lanes picked their pre-land tests by hand and by name, and lost whole land gates (a slot wait and
  ~8 min of suite) to the one they missed: GOH.ONEADMISSION's first land (500 s of its 1089 s) to
  `tests/test_lock_order.py`, which runs the changed `tools/load.py` by path; GOH.TAGPUSHGATE's to
  `tests/test_self_walk.py`, a tree reader no name pattern sees. `tools/affected_tests.py` chooses
  the set from the diff against main: every test that names a changed path (the path, or each of
  its components quoted, `REPO_ROOT / "tools" / "load.py"`; a root file only as `REPO_ROOT / name`)
  or imports a changed module (ast, tests/ helpers included), the changed tests, and every tree
  reader (a `test_every_`/`test_no_` test, a test or helper that lists or walks the tree, plus
  `ALWAYS`). `tools/land.sh` runs a repo's declared `GOH_LAND_PRE` (goh's: `affected_tests.py
  --run`) before the land lock, admitted by a gate slot of its own (a 0-spinner `load.py` run,
  `lib/admission.py`) and bounded at 10 min; a red one lands nothing. Replayed on ONEADMISSION's
  first-attempt diff, it chooses `tests/test_lock_order.py` ("names tools/load.py"). Pinned by
  `tests/test_affected_tests.py`.
