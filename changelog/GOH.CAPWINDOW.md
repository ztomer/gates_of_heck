* `goh capacity`, the lane-room spawn hook and the loop watch judge CPU idle over one rolling
  window: every reading records its tick sample in `~/.local/state/goh/cpu_idle.json`
  (`GOH_CPU_IDLE_STATE`), one self-exiting sampler keeps it warm, and the idle is the mean since
  the newest sample at least `GOH_CPU_IDLE_ROLLING_S` (60 s) old. The printed line names the
  window and sample count; the 2 s sample is only the fallback while the state is cold. A machine
  flapping between busy and idle no longer admits on an idle instant.
