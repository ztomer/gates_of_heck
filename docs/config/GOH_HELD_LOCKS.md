# GOH_HELD_LOCKS
Area: pushes
Kind: internal
Order: 560
Default: set by every holder of a goh lock

`name@pid[:key],...`: the locks of `lib/lock_order.tsv` this process's ancestors hold, exported by
`lib/lock_order.py`/`.sh` (and `tools/load.py` to its command), so a wait on a lock ranked at or
below a held one is refused, naming both, before it can deadlock (GOH.LOCKORDER). An entry whose pid
is dead is ignored. `key` names the repository the lock was taken in (a hash of its git common dir):
a `repo`-scoped lock of another repository is not ordered against it (GOH.LOCKNEST). A 0-spinner
`tools/load.py` under a live ancestor's `load` entry runs in that ancestor's slot; any other
0-spinner run is admitted by a gate slot (`GOH_GATE_SLOTS`, GOH.ONEADMISSION). Not a setting.
