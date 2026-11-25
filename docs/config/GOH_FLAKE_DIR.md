# GOH_FLAKE_DIR
Area: verdict-store
Kind: setting
Order: 10
Default: `flakes/` in the state root

The flake register's root (`lib/flakes.py`, GOH.FLAKEREGISTER): its rows are goh.db's `flakes` table
in the goh.db beside it (`GOH_DB` when set; GOH.STATEDBS3); its `register.jsonl` and `by-test/`
files are read only until their import, and the per-test locks stay in `by-test/`. A test points it
at a temp dir.
