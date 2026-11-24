-- goh.db 0006 (GOH.DURATIONSDB): the suite's timing tables, one row per reading (lib/durations.py).
-- Three whole-file JSON tables (test-durations.json and its .poles / .poles-cpu siblings) were
-- read, merged and replaced by every top-level run with no lock, so two runs ending together kept
-- one run's readings. Here a run INSERTs its readings; the table a reader sees is the `durations`
-- view, each (kind, node)'s newest reading that no later prune hid.
--   kind      dur (call+setup+teardown seconds), pole (quiet seconds), pole_cpu (its CPU seconds)
--   loaded    1 when the reading was taken above lib/long_pole.py QUIET_LOAD
-- The writer keeps the newest 5 readings per (kind, node) (lib/durations.py KEEP); the bound below
-- is the 30 days and the byte budget around that.
-- bound: readings days=30 rows=1000000 bytes=96MB stamp=at
CREATE TABLE readings(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  run TEXT NOT NULL,
  node TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('dur', 'pole', 'pole_cpu')),
  seconds REAL NOT NULL,
  load REAL,
  loaded INTEGER NOT NULL DEFAULT 0,
  at REAL NOT NULL
);
CREATE INDEX readings_kind_node ON readings(kind, node, n);
CREATE INDEX readings_at ON readings(at);
-- A prune by collected node id (GOH.DURATIONSPRUNE): the node's readings up to AT leave the view;
-- a later reading brings it back. Written in the same transaction as the run's readings.
-- bound: pruned days=30 rows=200000 bytes=16MB stamp=at
CREATE TABLE pruned(
  kind TEXT NOT NULL CHECK(kind IN ('dur', 'pole', 'pole_cpu')),
  node TEXT NOT NULL,
  run TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY (kind, node)
);
CREATE INDEX pruned_at ON pruned(at);
-- The split's ledgers beside the tables (plan decision 4): remote_rates (lib/remote_lane.py
-- Rates) and linux_unsafe ({module: why}, lib/remote_gate.py), one JSON value per key. Not named
-- `kv`: goh.db 0004 (GOH.STATEDB S3) owns that name, and migrations apply by set difference.
-- bound: duration_kv days=400 rows=100 bytes=16MB stamp=at
CREATE TABLE duration_kv(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE VIEW durations AS
SELECT kind, node, seconds, run, at FROM (
  SELECT kind, node, seconds, run, at,
         ROW_NUMBER() OVER (PARTITION BY kind, node ORDER BY at DESC, n DESC) AS rk
  FROM readings
) AS r
WHERE rk = 1
  AND NOT EXISTS (SELECT 1 FROM pruned AS p
                  WHERE p.kind = r.kind AND p.node = r.node AND p.at >= r.at);
