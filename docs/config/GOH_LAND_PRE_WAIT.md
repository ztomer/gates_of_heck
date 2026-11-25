# GOH_LAND_PRE_WAIT
Area: pushes
Kind: setting
Order: 80
Default: none

A seam for the bound on the pre-step's wait for a gate slot (`tools/load.py --wait-max`; unset:
load.py's default, 15 min). A wait that expires means the set never ran: `tools/land.sh` says `no
verdict: waited N s for a gate slot and ran nothing` and the gate decides, the deadline's own
no-verdict path, never "the pre-land tests failed" (GOH.PREWAITVERDICT). The request carries
`pre_verdict` {state, kind, why}.
