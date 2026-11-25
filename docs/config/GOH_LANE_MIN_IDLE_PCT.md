# GOH_LANE_MIN_IDLE_PCT
Area: claude-hooks
Kind: setting
Order: 30
Default: `20`

The measured CPU idle percent under which there is no room (LOOP.FILLIDLE, owner 2026-11-21 "we're
at 60% idle cpu"): the host's own tick counters (`HOST_CPU_LOAD_INFO` on macOS, `/proc/stat` on
Linux) sampled twice, `GOH_CPU_IDLE_WINDOW_S` apart. It replaced the load average as the judge:
macOS counts threads blocked on I/O in the load, and the load rule refused at load 63.6 on 16 cores
while the CPU was 72% idle.
