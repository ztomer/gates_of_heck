# GOH_SLEEPTIME_FILE
Area: pushes
Kind: setting
Order: 10
Default: unset (macOS `kern.sleeptime`)

A file holding the last sleep's epoch seconds, read in place of the OS counter by
`lib/sleep_mark.sh`: the seam the tests plant a sleep through. When a push gate or a round's
preflight fails and the counter moved during the run, the failure is followed by a warning that the
machine slept, because a dark wake fails display, GPU and clock-reading tests for the sleep's sake.
Linux has no counter, so nothing is said there.
