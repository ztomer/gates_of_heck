-- old-writers: safe
-- goh.db 0024 (GOH.TIMEACCOUNT): the time account's rows (lib/run_ends.py, lib/cpu_owners.py).
-- `run_ends`: one row per START of a check, pre-step, land, idle run or ship, written when it
-- starts (`outcome` NULL: open) and closed when it ends -- finished, red, stopped, refused or
-- carried -- by its own process, else by `sweep` once `pid` (born at `pid_start`) is gone. `cause`
-- names why a run that did not finish was thrown away; the tests chosen, reused and refused, and
-- the read that refused them, are columns. `run_phases`: a run's named phases (a pre-step's wait
-- and run, an enqueue check's commit gates), each with seconds, cpu-s and the load at its start.
-- `cpu_owners`: one row per owner per sampler window: the cpu-s that owner's processes burnt.
-- bound: run_ends days=400 rows=50000 stamp=at
-- with: run_phases of run_ends
-- bound: cpu_owners days=60 rows=100000 stamp=at
CREATE TABLE run_ends(
  kind TEXT NOT NULL CHECK (kind IN ('check', 'prestep', 'land', 'idle', 'ship')),
  run TEXT NOT NULL,
  repo TEXT,
  item TEXT,
  pid INTEGER,
  pid_start REAL,
  started REAL NOT NULL,
  ended REAL,
  outcome TEXT CHECK (outcome IN ('finished', 'red', 'stopped', 'refused', 'carried')),
  cause TEXT,
  wall_s REAL CHECK (wall_s >= 0),
  cpu_s REAL,
  load1 REAL,
  tests_chosen INTEGER,
  tests_reused INTEGER,
  tests_refused INTEGER,
  refused_by TEXT,
  at REAL NOT NULL,
  PRIMARY KEY (kind, run)
) STRICT, WITHOUT ROWID;
CREATE INDEX run_ends_open ON run_ends(outcome, kind);
CREATE INDEX run_ends_at ON run_ends(at);
CREATE TABLE run_phases(
  kind TEXT NOT NULL,
  run TEXT NOT NULL,
  phase TEXT NOT NULL,
  ord INTEGER NOT NULL,
  seconds REAL NOT NULL CHECK (seconds >= 0),
  cpu_s REAL,
  load1 REAL,
  PRIMARY KEY (kind, run, phase)
) STRICT, WITHOUT ROWID;
CREATE TABLE cpu_owners(
  at REAL NOT NULL,
  window_s REAL NOT NULL CHECK (window_s > 0),
  owner TEXT NOT NULL,
  cpu_s REAL NOT NULL CHECK (cpu_s >= 0),
  PRIMARY KEY (at, owner)
) STRICT, WITHOUT ROWID;
CREATE INDEX cpu_owners_at ON cpu_owners(at);
