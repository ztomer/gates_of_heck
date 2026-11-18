* The pre-step's selection is audited (GOH.SELECTAUDIT): every red land attempt is read against
  the selection its tip's own selector makes for its diff, and each failed test it did not choose
  is a row of `<landq>/selection_misses.jsonl`; the idle full run on main is shadowed the same way
  for the diff since the previous run, a measure and never a skip. `goh land --report` prints the
  misses per day. 2026-11-17 replayed from the landq logs: 2 of 19 reds were misses (11 tests:
  GOH.BUILDINCLUDES' 10 in test_rust_gate.py, GOH.GRACESHAPES' 1), both closed since; the
  ADAPTLANES, PREPARITY and CAPHEADROOM reds were in tests the selection chose.
* A test that reads a directory at run time is chosen by a change inside it (lib/declared_reads.py):
  a spelled directory globbed or copied whole (a change under tests/fixtures/swift_baseline_project
  chose nothing before), or a declared `TREE_READS = (...)`; gates/undeclared_reads.txt ratchets the 70
  tests whose joined paths no spelling shows.
