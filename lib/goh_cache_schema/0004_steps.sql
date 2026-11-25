-- old-writers: safe
-- bound: evict_objs rows=20000 stamp=at
-- bound: canary_memo days=30 rows=5000 stamp=at
-- bound: verified_shas days=400 rows=50000 stamp=at
-- bound: steps days=28 rows=150000 bytes=48MB stamp=at
CREATE TABLE steps(
  run TEXT NOT NULL, part TEXT NOT NULL DEFAULT '', seq INTEGER NOT NULL CHECK (seq >= -1), at REAL NOT NULL,
  label TEXT NOT NULL, tier TEXT NOT NULL, kind TEXT, parent TEXT, dir TEXT, ms REAL NOT NULL CHECK (ms >= 0), cpu_ms REAL,
  rc INTEGER NOT NULL, control INTEGER NOT NULL DEFAULT 0 CHECK (control IN (0, 1)), calls INTEGER NOT NULL DEFAULT 1 CHECK (calls >= 1),
  load1 REAL, cache TEXT, db TEXT, pid INTEGER, wait_s REAL,
  n INTEGER PRIMARY KEY
) STRICT;
CREATE UNIQUE INDEX steps_once ON steps(run, part, seq);
CREATE INDEX steps_at ON steps(at);
-- bound: remote_secs days=90 rows=50000 bytes=8MB stamp=at
CREATE TABLE remote_secs(
  repo TEXT NOT NULL,
  node TEXT NOT NULL,
  seconds REAL NOT NULL CHECK (seconds >= 0),
  at REAL NOT NULL,
  PRIMARY KEY (repo, node)
) STRICT, WITHOUT ROWID;
CREATE INDEX remote_secs_at ON remote_secs(at);
CREATE VIEW steps_v1 AS SELECT run, part, seq, at, label, tier, kind, parent, dir, ms, cpu_ms, rc, calls FROM steps WHERE control = 0;
CREATE TABLE evict_objs(path TEXT PRIMARY KEY, uses REAL NOT NULL, l REAL NOT NULL, at REAL NOT NULL) STRICT, WITHOUT ROWID;
CREATE TABLE canary_memo(kind TEXT NOT NULL CHECK (kind IN ('grace', 'release')), key TEXT NOT NULL, value TEXT NOT NULL CHECK (json_valid(value)),
  at REAL NOT NULL, PRIMARY KEY (kind, key)) STRICT, WITHOUT ROWID;
CREATE TABLE verified_shas(session TEXT NOT NULL, tool_use_id TEXT NOT NULL, ord INTEGER NOT NULL, sha TEXT NOT NULL, at REAL NOT NULL,
  PRIMARY KEY (session, tool_use_id, ord)) STRICT, WITHOUT ROWID;
