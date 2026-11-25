# GOH_GATE_SLOT_STARVE_MIN
Area: pushes
Kind: setting
Order: 460
Default: `15` (goh's `.gatesrc` names it)

The gate slots' starvation bound, in minutes (GOH.SLOTPRIORITY, `lib/gate_lanes.py`): a waiter of
any class that has waited this long goes ahead of every class, oldest first, so a sweep or another
repo's gate waits behind lands for at most this long. Read by each waiter from the environment, else
its repository's `.gatesrc`, else 15, and written into its own ticket, so every reader orders the
queue alike. Anything but a positive number refuses the admission.
