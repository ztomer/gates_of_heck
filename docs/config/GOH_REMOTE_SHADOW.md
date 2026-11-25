# GOH_REMOTE_SHADOW
Area: remote-worker
Kind: setting
Order: 150
Default: unset (one gate in ten)

The land gate's sampled shadow (GOH.REMOTEGATE, `lib/remote_gate.py`): `1` makes every gate a
shadow, `0` none. A shadow gate runs its remote share's files on the Mac too (the Mac's verdict
stands), and a module whose verdict differs between the platforms is put in the disagreement ledger
(goh.db's `linux_unsafe` beside the duration table), which takes it out of `tests/linux_safe.txt`
for every later split.
