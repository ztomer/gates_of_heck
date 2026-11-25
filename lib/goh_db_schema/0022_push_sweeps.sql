-- old-writers: safe
-- goh.db 0022 (GOH.PINSWEEP): the consumers' pin push sweeps (lib/push_sweep_rows.py), once a
-- `<result>.sweep.json` beside each ship. `sweep` is that file's path, the name every record has
-- for it; `run` is the start that made the row, so a supervisor of an earlier one knows it is not
-- its own. `quiet` and `every` are the schedule it was started under (lib/pin_schedule.py).
-- `ship_worker`, `supervisor` and `runner` are processes as lib/pid_owner.py stamps them.
-- A repo's row: `started` to `at` is its turn, `slot_wait_s` the part it waited for a gate slot,
-- `tip` its newest commit that is no pin commit, when a gate judged it. A step: one the gate named
-- as failed, and goh's `closure` for it then (lib/push_hold.py).
-- bound: push_sweeps days=400 rows=5000 stamp=at
-- with: push_sweep_repos of push_sweeps
-- with: push_sweep_steps of push_sweep_repos
CREATE TABLE push_sweeps(
  sweep TEXT PRIMARY KEY,
  run TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
  created REAL NOT NULL,
  quiet REAL NOT NULL CHECK (quiet >= 0),
  every REAL NOT NULL CHECK (every >= 0),
  due REAL,
  started REAL,
  finished REAL,
  budget REAL NOT NULL,
  ship TEXT,
  ship_worker TEXT,
  supervisor TEXT,
  runner TEXT,
  exit INTEGER,
  why TEXT,
  log TEXT,
  at REAL NOT NULL
) STRICT;
CREATE INDEX push_sweeps_status ON push_sweeps(status);
CREATE INDEX push_sweeps_created ON push_sweeps(created);
CREATE INDEX push_sweeps_at ON push_sweeps(at);
CREATE TABLE push_sweep_repos(
  sweep TEXT NOT NULL,
  place TEXT NOT NULL,
  repo TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'pushing', 'pushed', 'refused', 'held', 'committed', 'superseded')),
  said TEXT,
  log TEXT,
  started REAL,
  seconds REAL,
  slot_wait_s REAL,
  tip TEXT,
  at REAL,
  PRIMARY KEY (sweep, place)
) STRICT, WITHOUT ROWID;
CREATE INDEX push_sweep_repos_repo ON push_sweep_repos(repo, at);
CREATE TABLE push_sweep_steps(
  sweep TEXT NOT NULL,
  place TEXT NOT NULL,
  ord INTEGER NOT NULL,
  step TEXT NOT NULL,
  closure TEXT,
  PRIMARY KEY (sweep, place, ord)
) STRICT, WITHOUT ROWID;
