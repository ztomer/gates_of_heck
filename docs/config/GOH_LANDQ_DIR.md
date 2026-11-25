# GOH_LANDQ_DIR
Area: ship
Kind: setting
Order: 130
Default: `~/.local/state/goh/landq`

The landing queue `goh land` keeps (`tools/landq.py`, GOH.LANDQUEUE, D-0006): `pending/` requests by
enqueue time, `running/`, `done/` outcomes, `logs/`, and `lander.lock`, the flock the ONE lander
holds. A test seam.
