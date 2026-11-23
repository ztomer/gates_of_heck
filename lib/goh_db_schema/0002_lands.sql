-- goh.db 0002 (GOH.STATEDB S1): the land records, out of <landq>/done/*.json, guards.jsonl,
-- red_causes.json and selection_misses.jsonl. lib/land_records.py is their one door.
-- `queue` is the land queue's directory (its realpath), so a test's queue under tmp_path and the
-- lander's own never share a row; `run` is the request stem (`01795463637285577000-ITEM`).
-- `at` is the record's epoch seconds (its `at`, else `finished`), the column the bounds read.
-- lands keeps every summary row (plan decision 6): past its bytes the oldest move to
-- goh-archive-YYYY.db, never away.
-- bound: lands bytes=128MB stamp=at
CREATE TABLE lands(
  queue TEXT NOT NULL,
  run TEXT NOT NULL,
  item TEXT,
  sha TEXT,
  enqueued TEXT,
  repo TEXT,
  mode TEXT,
  state TEXT,
  attempt INTEGER,
  requested TEXT,
  started TEXT,
  finished TEXT,
  k INTEGER,
  at REAL NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY (queue, run)
);
CREATE INDEX lands_item ON lands(item);
CREATE INDEX lands_sha ON lands(sha);
CREATE INDEX lands_repo_finished ON lands(repo, finished);
CREATE INDEX lands_state ON lands(state);
CREATE INDEX lands_at ON lands(at);
-- bound: land_guards days=14 rows=5000 bytes=8MB stamp=at
CREATE TABLE land_guards(
  n INTEGER PRIMARY KEY,
  queue TEXT NOT NULL,
  at REAL NOT NULL,
  run TEXT,
  test TEXT,
  body TEXT NOT NULL
);
CREATE INDEX land_guards_queue ON land_guards(queue, n);
-- bound: red_causes days=400 rows=20000 bytes=16MB stamp=at
CREATE TABLE red_causes(
  queue TEXT NOT NULL,
  run TEXT NOT NULL,
  cause TEXT,
  at REAL NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY (queue, run)
);
-- bound: selection_misses days=35 rows=20000 bytes=32MB stamp=at
CREATE TABLE selection_misses(
  n INTEGER PRIMARY KEY,
  queue TEXT NOT NULL,
  day TEXT,
  at REAL NOT NULL,
  run TEXT,
  body TEXT NOT NULL
);
CREATE INDEX selection_misses_queue ON selection_misses(queue, n);
-- The interface other readers use (handover plan (d)): changed only by adding lands_v2 beside it.
CREATE VIEW lands_v1 AS
  SELECT run, item, sha, enqueued, repo, mode, state, attempt, requested, started, finished, k,
         at, body FROM lands;
