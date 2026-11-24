-- goh.db 0003 (GOH.STATEDB S2): ship records and their pin sidecars (lib/ship_records.py).
-- One row per `goh ship` result, rewritten at each step (lib/ship_status.py save). A record is
-- named by its RESULT path everywhere (GOH_SHIP_RESULT, `goh ship --status RESULT`, loopctl's
-- --shipped), so the path is the key; `run` is its stem (20261124T020519Z-c1916d44), the join key
-- of handover plan (d). The step logs stay files beside the result, named in `body`.
--   repo      the repo's ship directory name (ship/<repo>/)
--   at        epoch seconds of the row's last write: the bound's stamp
-- bound: ships days=90 rows=20000 bytes=64MB stamp=at
CREATE TABLE ships(
  result TEXT PRIMARY KEY,
  run TEXT NOT NULL,
  repo TEXT NOT NULL,
  item TEXT,
  sha TEXT,
  status TEXT,
  started TEXT,
  finished TEXT,
  version TEXT,
  body TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX ships_run ON ships(run);
CREATE INDEX ships_item ON ships(item);
CREATE INDEX ships_sha ON ships(sha);
CREATE INDEX ships_repo_started ON ships(repo, started);
CREATE INDEX ships_at ON ships(at);
-- The install step's pin record (lib/consumer_pins.py write_record), once `<result>.pins.json`:
-- what each consumer's pin commit did, carried into the ship's record as consumer_pins.
-- bound: ship_pins days=90 rows=20000 bytes=16MB stamp=at
CREATE TABLE ship_pins(
  result TEXT PRIMARY KEY,
  run TEXT NOT NULL,
  body TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX ship_pins_at ON ship_pins(at);
-- The versioned view other readers use (handover plan (d)): changed only by adding ships_v2.
CREATE VIEW ships_v1 AS
SELECT run, result, repo, item, sha, status, started, finished, version, at FROM ships;
