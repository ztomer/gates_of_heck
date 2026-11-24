-- goh-cache.db 0002 (GOH.VERDICTDB V1): goh's Python verdict stores, each file a row.
-- verdicts: lib/reuse.py's records (lib/reuse_store.py; was ~/.local/state/goh/reuse/<path>/
-- <kk>/<key>.json): one row per (registered path, key), body the whole record as it was a file.
-- reuse.prune() keeps each path inside its own TTL (reuse.ttl) and row cap (reuse_store.CAP); the
-- bound below is the table's, the longest TTL (`pushed`, a year). Measured 2026-11-24: 1.6k
-- records, 200 MB, 164 MB of it the 136 `carry` records of one day.
-- bound: verdicts days=366 rows=40000 bytes=1GB stamp=epoch
CREATE TABLE verdicts(
  path TEXT NOT NULL,
  key TEXT NOT NULL,
  fields TEXT NOT NULL,
  verdict TEXT NOT NULL,
  epoch INTEGER NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY(path, key)
) WITHOUT ROWID;
CREATE INDEX verdicts_epoch ON verdicts(path, epoch);
-- kept: the suite's kept verdict stores (tests/_kept_store.py: self-walk-verdicts, parse-findings,
-- tree-lint-verdicts), 30 days (STORE_DAYS) like the files they replace.
-- bound: kept days=30 rows=60000 bytes=256MB stamp=at
CREATE TABLE kept(
  store TEXT NOT NULL,
  key TEXT NOT NULL,
  body TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY(store, key)
) WITHOUT ROWID;
CREATE INDEX kept_at ON kept(store, at);
-- memo: lib/identity_cache.py's memo (was ~/.cache/goh/identity/), 512 per kind (KEEP).
-- bound: memo days=30 rows=8192 bytes=64MB stamp=at
CREATE TABLE memo(
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY(kind, key)
) WITHOUT ROWID;
CREATE INDEX memo_at ON memo(kind, at);
