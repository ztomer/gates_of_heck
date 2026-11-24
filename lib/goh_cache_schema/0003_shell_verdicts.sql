-- goh-cache.db 0003 (GOH.VERDICTDB V2): the goh binary's per-file verdicts
-- (crates/goh/src/verdict_cache.rs; was ~/.cache/goh/verdicts/<checker>/<kk>/<sha>.json, 176k files,
-- 1.3 GB, unbounded). One row per (checker, key): `key` is the sha256 of the checker's whole key
-- (every input of the verdict; measured 2026-11-24 at 9.3 kB a shell-lint key, against a 30-byte
-- verdict), `record` the verdict as JSON, `at` when it was last written. The binary reads and
-- writes it in-process (rusqlite, bundled SQLite) and never migrates: lib/goh_cache_db.py does.
-- bound: shell_verdicts days=30 rows=200000 bytes=256MB stamp=at
CREATE TABLE shell_verdicts(
  checker TEXT NOT NULL,
  key TEXT NOT NULL,
  record TEXT NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY(checker, key)
) WITHOUT ROWID;
CREATE INDEX shell_verdicts_at ON shell_verdicts(checker, at);
