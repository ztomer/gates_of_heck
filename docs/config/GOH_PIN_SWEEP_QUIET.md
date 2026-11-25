# GOH_PIN_SWEEP_QUIET
Area: ship
Kind: setting
Order: 61
Default: `21600`

Seconds a release's pin pushes wait for no other release to follow it (`lib/pin_schedule.py`,
GOH.PINSWEEP): the install still commits each consumer's pin, and the pushes, each through that
consumer's full push gate, run once a sweep is due. A newer release's sweep takes every repo it
lists, and its push carries the older pin commits. Read from the environment, then goh's `.gatesrc`;
`0` runs them as soon as the ship returns. A value that is no number of seconds refuses
`goh wf-pin` before it commits anything.
