# GOH_LAND_PRE_ONTO
Area: pushes
Kind: setting
Order: 130
Default: unset

Under `GOH_LAND_PRE_ONLY`, `tools/land.sh` rebases the checkout onto main before its pre-step, so
the enqueue judges the tree the lander will gate unless main moves after it; a conflict is red at
enqueue. Set only for goh's own detached checkout of the request (`lib/land_slot.py`), never a
lane's tree; read once and unset (GOH.PREONTIP). Internal: the queue sets it.
