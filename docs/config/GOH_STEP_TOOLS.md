# GOH_STEP_TOOLS
Area: local-ci
Kind: setting
Order: 20
Default: unset

Space-separated tools this repo's `GOH_CI_STEPS` run that it installs ITSELF (ztools: `cargo-deny`).
A step's tool must be in a table (GOH.REQTOOLS, `gates/step_tools.py`): `gates/required_tools.tsv`,
`step_tools.SYSTEM` (what every runner image carries), or this key; from 2026-11-23 an unnamed one
is refused by `required_tools.py --repo` (the hosted install) and by `local_ci.sh` before any step
runs, a warning before.
