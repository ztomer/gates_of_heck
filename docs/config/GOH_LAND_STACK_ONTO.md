# GOH_LAND_STACK_ONTO, GOH_LAND_STACK_UPSTREAM
Area: pushes
Kind: setting
Order: 210
Default: unset

Set together, `tools/land.sh` only rebases its branch: its own commits above
`GOH_LAND_STACK_UPSTREAM` onto `GOH_LAND_STACK_ONTO`, with the row merge driver and the conflict
message a land has, then exits: no pre-step, no lock, no gate, no ledger row. The landing queue's
batch stacks each item onto the one below it this way (GOH.LANDBATCH, `lib/landq_batch.py`).
Internal: the lander sets them.
