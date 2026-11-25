# GOH_LAND_LOCK_OWNER
Area: pushes
Kind: setting
Order: 400
Default: unset

`<lock path> <pid>`, set by the process that holds goh's land lock (`lib/land_lock.py`, goh ship's
release kit) for its children only, never in `.gatesrc`: a `tools/land.sh` run under it whose lock
file names that same live pid is the holder's own land and does not wait on the lock it already
holds (GOH.CLASS.CHECKOUT). Any other value, or a dead pid, is ignored and the land queues as usual.
