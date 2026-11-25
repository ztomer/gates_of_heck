# GOH_REMOTE_GATE_WAIT_S
Area: remote-worker
Kind: setting
Order: 160
Default: `300`

How long the land gate's suite, its own run done, waits for its remote share (GOH.REMOTEGATE,
`lib/remote_gate.py`) before it kills the share and runs those files on the Mac: a hung or
far-behind worker never holds a gate for remote-shard's hour-long run timeout.
