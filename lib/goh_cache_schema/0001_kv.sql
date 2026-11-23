-- goh-cache.db 0001 (GOH.DBSEAM): one disposable value per name (the plan's decision 4 puts
-- cpu_idle here). A cache table past its bound evicts its oldest rows.
-- bound: kv days=30 rows=10000 bytes=64MB stamp=at
CREATE TABLE kv(
  name TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX kv_at ON kv(at);
