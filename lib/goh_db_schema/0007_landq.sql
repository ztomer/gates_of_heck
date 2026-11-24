-- goh.db 0007 (GOH.LANDQDB): the land queue's live state, out of <landq>/{enqueuing,pending,running}/
-- *.json, slots/<repo>/<n>.owner and early/. lib/landq_state.py is its one writer (land_slot.py the
-- owners'). pre/*.record stays a file beside its verdict copy for now: land.sh and pre_reuse.py
-- read it by path, so its move is a migration of its own.
-- A request is ONE row whose `state` is one of three: a move between states is one UPDATE and
-- finishing a land is one transaction (DELETE FROM requests, INSERT INTO lands), so no kill leaves
-- a request in two states or in none (two renames did: running/ and done/ after a kill between).
-- `queue` is the land queue's realpath, as in `lands` (0002): a test's queue never reads the
-- lander's rows. `run` is the request stem (`<ns>-<item>`); an enqueue's marker is `<pid>-<item>`.
-- A live queue holds a handful of rows: the bounds only stop a runaway, never age a request out.
-- bound: requests rows=10000 bytes=16MB stamp=filed_at
CREATE TABLE requests(
  queue TEXT NOT NULL,
  run TEXT NOT NULL,
  item TEXT,
  repo TEXT,
  sha TEXT,
  mode TEXT,
  state TEXT NOT NULL CHECK (state IN ('enqueuing', 'pending', 'running')),
  pid INTEGER,
  pid_stamp TEXT,
  until REAL,
  filed_at REAL NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY (queue, run)
);
CREATE INDEX requests_state ON requests(queue, state, run);
CREATE INDEX requests_item ON requests(item);
-- A checkout slot's owner (lib/land_slot.py), decided in the transaction that reads `requests`:
-- `repo_key` is the slots root (`<q>/slots/<repo>-<hash>`), `n` the slot number.
-- bound: slot_owners rows=10000 bytes=4MB stamp=at
CREATE TABLE slot_owners(
  repo_key TEXT NOT NULL,
  n INTEGER NOT NULL,
  token TEXT NOT NULL,
  pid INTEGER,
  pid_stamp TEXT,
  at REAL NOT NULL,
  PRIMARY KEY (repo_key, n)
);
-- bound: early rows=10000 bytes=16MB stamp=at
CREATE TABLE early(
  queue TEXT NOT NULL,
  run TEXT NOT NULL,
  at REAL NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY (queue, run)
);
-- The interface other readers use (handover plan (d)): what is in flight, by state. The loop's
-- in-flight reading (.loop tools/goh_view.py in_flight) counts an enqueuing row only while its
-- pid (by pid_stamp) is alive and `until` has not passed, as goh's own lib/landq_inflight.py does.
CREATE VIEW landq_v1 AS
  SELECT queue, run, item, repo, sha, mode, state, pid, pid_stamp, until, filed_at FROM requests;
