# GOH_TIMING_VIA
Area: runtime
Kind: internal
Order: 210
Default: set by `tools/land.sh`

Names the caller whose own ledger row holds the push gate it runs, so the gate writes none (a land
is one row, not a land and a push). Not a setting: written for the gate `tools/land.sh` runs, read
by `gates/push_gate.sh`.
