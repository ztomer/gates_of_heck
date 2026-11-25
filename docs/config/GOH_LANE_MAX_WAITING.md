# GOH_LANE_MAX_WAITING
Area: claude-hooks
Kind: setting
Order: 20
Default: `1`

Long-lane gates waiting for a gate slot (live tickets in `<GOH_GATE_SLOT_DIR>/waiting/`) above which
there is no room: a second waiter means a full round of both slots is queued. Commit gates' short
lane is not counted. Calibration below.
