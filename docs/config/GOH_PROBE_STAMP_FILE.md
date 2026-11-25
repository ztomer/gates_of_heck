# GOH_PROBE_STAMP_FILE
Area: runtime
Kind: setting
Order: 430
Default: unset

Where the instrumented writer stamps when each of its writes happened (a file per pid), so the probe
can measure the window at the writer instead of inferring it from how often it was caught. Not user
config.
