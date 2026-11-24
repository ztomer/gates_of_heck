-- goh.db 0004 (GOH.STATEDBS3): goh's small state stores, behind lib/small_stores.py.
-- kv: one JSON document per name, each a whole-file JSON store before the switch, rewritten whole
-- by its writer under that writer's lock (lib/estate_registry.py update_state, lib/cache_evict.py):
--   estate_audit   the estate audit's and the release canary's verdicts (the SessionStart hook)
--   pin_push       every repo whose pin commit a sweep left unpushed (lib/pin_sweep.py)
--   pin_sweeps     the pin sweeps started and not yet done (lib/pin_sweep.py)
--   cache-evict    the cache evictor's ledger: uses and clocks by object (lib/cache_evict.py)
--   wf_pin.pins    `goh wf-pin`'s consumer pin record (lib/consumer_pins.py)
-- The largest (estate_audit) is 1.5 MB; the budget holds every name many times over.
-- bound: kv days=400 rows=1000 bytes=64MB stamp=at
CREATE TABLE kv(
  name TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  at REAL NOT NULL
);
-- Every tree a commit gate passed (lib/commit_stamp.py; was commit-gated.jsonl): goh land
-- --check runs the gate on a commit whose tree is not here. body is the row as it was a line.
-- bound: commit_gated days=35 rows=20000 bytes=16MB stamp=at
CREATE TABLE commit_gated(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  tree TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX commit_gated_tree ON commit_gated(tree);
CREATE INDEX commit_gated_at ON commit_gated(at);
-- The flake register (lib/flakes.py; was flakes/register.jsonl and flakes/by-test/*.jsonl): one
-- row per pass-on-retry ({id, test, ...}) and one per filing of a test's item ({filed, epoch}),
-- in the order written; a test's 2-in-14-days rule reads its own rows by the index on test.
-- bound: flakes days=35 rows=20000 bytes=16MB stamp=epoch
CREATE TABLE flakes(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  epoch REAL NOT NULL,
  test TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX flakes_test ON flakes(test, n);
CREATE INDEX flakes_epoch ON flakes(epoch);
