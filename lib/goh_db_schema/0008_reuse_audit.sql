-- goh.db 0008 (GOH.VERDICTDB V1): the reuse audit, behind lib/reuse_audit_store.py. Not a cache:
-- a path's record says it is OFF (lib/reuse_audit.py, "OFF IS ONE RECORDED STATE"), and losing it
-- would turn a wrong reuse back on, so it lives in the durable file, not goh-cache.db.
-- reuse_audit: one record per registered reuse path (was reuse/audit/<path>.json), its body the
-- whole record. No age bound: an off path stays off until `enable`, however long that takes; ~15
-- paths, so the row cap is never reached by a real store.
-- bound: reuse_audit rows=1000 bytes=16MB stamp=at
CREATE TABLE reuse_audit(
  path TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  at REAL NOT NULL
);
-- reuse_shadow: every compared sample (was reuse/audit/shadow.jsonl), its body the line as it was;
-- the evidence a review reads, ~3000 rows a day, 14 days as the file's bound (lib/state_files.py).
-- bound: reuse_shadow days=14 rows=30000 bytes=64MB stamp=epoch
CREATE TABLE reuse_shadow(
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  epoch REAL NOT NULL,
  path TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX reuse_shadow_epoch ON reuse_shadow(epoch);
