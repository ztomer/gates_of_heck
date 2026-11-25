# GOH_STEP_CPU_TIMEOUT
Area: local-ci
Kind: setting
Order: 160
Default: `limit * ncpu`

Optional CPU-seconds override passed as `--cpu-timeout` for bounded gate steps (defaults to the
nominal ceiling multiplied by online CPUs, counting reaped descendants).
