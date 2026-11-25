# GOH_LAND_PRE_MAX
Area: pushes
Kind: setting
Order: 60
Default: `10m`

The pre-land step's deadline FLOOR: the deadline is `min(GOH_LAND_PRE_CEIL, max(GOH_LAND_PRE_MAX,
1.5 x WORK / workers))`, taken after admission from the share it gave (`lib/pre_deadline.py`,
GOH.PRESTEPSHARE); a pre-step still running then is no verdict, and the land goes on to the lock and
the gate (GOH.PRESTEPDEADLINE).
