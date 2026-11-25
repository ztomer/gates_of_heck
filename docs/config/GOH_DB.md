# GOH_DB
Area: pushes
Kind: setting
Order: 320
Default: `goh.db` in the state root

An absolute path: goh.db, goh's durable state (`lib/goh_db.py`, GOH.DBSEAM). A relative path is
refused. Tests point it at their own `tmp_path`; under a suite a file in the real home's goh state
directory is refused at open.
