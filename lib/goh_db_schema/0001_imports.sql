-- goh.db 0001 (GOH.DBSEAM): the import ledger every store's move writes (handover plan (b) step 2:
-- `goh_db.py import STORE` runs INSERT OR IGNORE on the store's natural key and records here what
-- it read, so a re-run is harmless and a file is deleted only after its rows read back).
-- bound: imports days=400 rows=100000 bytes=16MB stamp=at
CREATE TABLE imports(
  store TEXT NOT NULL,
  source TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  rows INTEGER NOT NULL,
  at REAL NOT NULL,
  PRIMARY KEY (store, source, sha256)
);
