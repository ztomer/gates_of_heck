# GOH_TIMING_DIR
Area: runtime
Kind: setting
Order: 200
Default: `$XDG_STATE_HOME/goh/timing`, else `~/.local/state/goh/timing`

Where the timing rows' spool is (an absolute path). A row per measured run of a subject (a push
gate, a land, a suite, a release step), written by `gates/push_gate.sh`, `tools/land.sh` and
`tools/ship.py` through `lib/timing_ledger.py`, is goh.db's `spans` (GOH.STEPTIMES); `ledger.jsonl`
here holds only what could not reach goh.db, and the next row that does takes it along. A named one
is its own ledger, put in no goh.db but the one `GOH_DB` names too. `goh budget` reads both
(GOH.GATEBUDGET).
