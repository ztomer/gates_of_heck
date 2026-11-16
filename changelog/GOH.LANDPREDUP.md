* **A green land runs each test once, and its pre-step only the tests that read the tree (GOH.LANDPREDUP).**
  GOH.TREETESTSFIRST's pre-step ran every tree-reading FILE whole (~175 files, 879 s of summed test
  time on goh's tree, 213-565 s of wall) before the land lock, and the full gate then ran them all
  again. `tools/affected_tests.py` now runs a tree reader the diff does not choose by its reading
  tests alone, as pytest node ids (`reader_nodes`: a `test_every_`/`test_no_` test, or one whose
  source reaches a walk through the file's own names, fixtures and decorators; the whole file when
  they cannot be told apart): 199 s summed for the same readers. The pre-step's suite records the
  tests it passed (`GOH_LAND_VERDICTS_RECORD`, `tests/_land_verdicts.py`) and the land's gate
  deselects them (`GOH_LAND_VERDICTS_REUSE`) when the record's identity is the gated checkout's
  (`lib/gate_identity.py suite`: the clean commit's tree and the tools); in the coverage step's
  instrumented run a test that ran the native binary is kept, so the crate's coverage is measured
  whole. A land rebased under its lock gates another tree and reuses nothing. Pinned by
  `tests/test_land_verdicts.py`.
