# GOH_LANE_LOAD_PER_CORE
Area: claude-hooks
Kind: setting
Order: 100
Default: `2.5`

SECONDARY: judges only when the CPU idle could not be measured. The 5-minute load average per core
above which there is no room (40 on a 16-core box). The 5-minute, not the 1-minute: a test burst
swings the 1-minute, and a lane starts its gates minutes after its spawn. Calibration below.
