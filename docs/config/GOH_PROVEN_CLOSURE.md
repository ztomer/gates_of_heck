# GOH_PROVEN_CLOSURE
Area: proven
Kind: setting
Order: 40
Default: `off`

`on` keys each proven step on its own goh closure (`lib/step_closure.py`, GOH.PROVENCLOSURE); `off`
keys every step on the whole goh content, as before, and computes no closure. Staged off
(GOH.PROVENCLOSURE2) until the reuse audit, which samples `step` (GOH.REUSEAUDIT2,
`gates/_proven_audit.sh`), has seen 3 ships with no disagreement. Any other value is an exit-2 usage
error.
