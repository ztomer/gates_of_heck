-- old-writers: safe
-- goh.db 0026 (GOH.TESTWORK): the work each test did, per run (lib/test_work.py, the one writer and
-- reader; tests/_test_work.py measures it). `test_work_runs`: one row per suite run the runner named
-- (tools/pytest.sh's top-level run): whether it ran the whole suite, its tests, the box's load at its
-- start and end and its cores. `test_work`: one row per test of it: CPU seconds of the test and the children it
-- waited for, the processes it started, the git repositories (`git init`/`clone`) and cargo builds
-- among them, the whole-tree walks it made, and whether it missed a kept store (`cold`: its counts
-- are the store refilling and are not judged). `<shared setup>` is the run's session fixtures.
-- bound: test_work_runs days=30 rows=400 bytes=64MB stamp=at
-- with: test_work of test_work_runs
CREATE TABLE test_work_runs(
  run TEXT NOT NULL PRIMARY KEY,
  whole INTEGER NOT NULL CHECK (whole IN (0, 1)),
  tests INTEGER NOT NULL,
  load1 REAL,
  load_end REAL,
  cores INTEGER,
  why TEXT,
  at REAL NOT NULL
) STRICT;
CREATE INDEX test_work_runs_at ON test_work_runs(at);
CREATE TABLE test_work(
  run TEXT NOT NULL,
  node TEXT NOT NULL,
  cpu_s REAL NOT NULL CHECK (cpu_s >= 0),
  procs INTEGER NOT NULL CHECK (procs >= 0),
  git_repos INTEGER NOT NULL CHECK (git_repos >= 0),
  cargo_builds INTEGER NOT NULL CHECK (cargo_builds >= 0),
  tree_walks INTEGER NOT NULL CHECK (tree_walks >= 0),
  cold INTEGER NOT NULL DEFAULT 0 CHECK (cold IN (0, 1)),
  PRIMARY KEY (run, node)
) STRICT, WITHOUT ROWID;
