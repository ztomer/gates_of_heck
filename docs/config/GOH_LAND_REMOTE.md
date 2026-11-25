# GOH_LAND_REMOTE
Area: remote-worker
Kind: setting
Order: 130
Default: `on`

`off` keeps the land pre-step AND the land gate's suite wholly on the Mac (GOH.REMOTEGATE: on, the
gate's whole run sends its Linux-safe files to the worker too, `lib/remote_gate.py`, and the
worker's verdict counts for the land; the ship still reruns everything on the Mac). On, the chosen
tests whose module `tests/linux_safe.txt` records run on the worker beside the Mac's share
(`lib/remote_lane.py`), skip parity against `tests/mac_skips.txt`; any fallback runs them on the
Mac, saying why.
