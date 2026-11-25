# GOH_COMMIT_LINT
Area: deps
Kind: setting
Order: 150
Default: unset

Set by `goh lint` (`tools/lint.py`) only: `gates/commit_gate.sh` runs as a lint over a temporary
index, skipping the hold and landed-only refusals and the seed settle (GOH.CLASS.PARITY). Not a
consumer setting.
