-- old-writers: safe
-- goh.db 0027 (GOH.BUILDONCE): one row per BUILD a run made of what it builds once (lib/build_once.py
-- `record`, written by the run's owner when it ends: `goh land --check`, `affected_tests.py --run`).
-- A run reuses each build in every later process; a reuse writes no row, and a second build is
-- refused (BuiltTwice), so a run's rows per kind are its builds: 1 is the invariant.
--   run      the run's directory name (goh-build-run.XXXX)
--   kind     goh-test-binary | dependency-map | template
--   tree     the checkout the run judged
--   caller   who built it (a test node, a function) and its pid
--   seconds  the build's wall seconds
-- bound: run_builds days=60 rows=200000 stamp=at
CREATE TABLE run_builds(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  run TEXT NOT NULL,
  kind TEXT NOT NULL,
  tree TEXT,
  caller TEXT,
  seconds REAL,
  at REAL NOT NULL
);
CREATE INDEX run_builds_at ON run_builds(at);
CREATE INDEX run_builds_run ON run_builds(run, kind);
