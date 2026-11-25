# GOH_LAND_BRANCH
Area: pushes
Kind: setting
Order: 160
Default: unset

The lane's branch, which `tools/land.sh` names in its lines when it runs in goh's detached checkout
of the enqueued sha (GOH.LANDSNAPSHOT, `lib/land_slot.py`); read once and unset, never in the gate's
environment. Unset, the checkout's own branch. Internal: the queue sets it.
