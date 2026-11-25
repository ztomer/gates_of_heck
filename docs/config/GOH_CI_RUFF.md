# GOH_CI_RUFF
Area: deps
Kind: setting
Order: 140
Default: `.github/workflows/gate.yml`

a shell variable of the hosted workflow's provisioning step, READ from the manifest
(`gates/required_tools.py --pin ruff`, GOH.CLASS.PARITY): the ONE ruff release the runner installs
into the house interpreter, asked by `gates/ruff_agree.py --pin` (GOH.CIRUFF).
