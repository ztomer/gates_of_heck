# GOH_SHIP_DIR
Area: ship
Kind: setting
Order: 110
Default: `~/.local/state/goh/ship`

Where results are named and their logs go: a result is `<dir>/<repo>/<UTC stamp>-<sha8>.json`, its
record a goh.db `ships` row under that path (`lib/ship_records.py`, GOH.STATEDBS2; the file is still
written beside it until the database plan's wave-3 delete), with `.log` (the worker's marks) and
`.<step>.log` beside it; `--out` names one.
