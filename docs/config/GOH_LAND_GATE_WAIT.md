# GOH_LAND_GATE_WAIT
Area: pushes
Kind: setting
Order: 90
Default: none

The same seam for the land gate's wait for a slot. An expired wait exits 75 (`the gate never ran ...
no verdict`), never "the gate failed"; the lander records it red with the reason and does not bisect
a batch for it (GOH.PREWAITVERDICT, `land_cause` `no-verdict`).
