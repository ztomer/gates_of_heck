# GOH_TIMING_DIR
Area: runtime
Kind: setting
Order: 200
Default: `$XDG_STATE_HOME/goh/timing`, else `~/.local/state/goh/timing`

The timing ledger's root (an absolute path): `ledger.jsonl`, one row per measured run of a subject
(a push gate, a land, a ship, its release step), written by `gates/push_gate.sh`, `tools/land.sh`
and `tools/ship.py` through `lib/timing_ledger.py`; `goh budget` reads it (GOH.GATEBUDGET).
