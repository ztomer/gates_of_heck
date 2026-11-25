# GOH_VERDICT_CACHE
Area: verdict-cache
Kind: setting
Order: 10
Default: unset (on)

`0`, `off`, `false` or `no` disables it: every file is judged by the tools, nothing is recorded. The
verdicts are rows of `GOH_CACHE_DB`'s `shell_verdicts` (GOH.VERDICTDB V2; `GOH_VERDICT_DIR`, one
JSON file per verdict, is retired): an off word or a relative `GOH_CACHE_DB` turns the cache off
too, and the binary never migrates the file (`lib/goh_cache_db.py migrate` does, once, before the
first write).
