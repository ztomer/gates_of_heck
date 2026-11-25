# GOH_ESTATE_ROOT
Area: deps
Kind: setting
Order: 70
Default: `~/Projects`

env, `scripts/estate_audit.py`: the directory whose git repositories (nested ones included, three
levels down) are audited, UNIONED with the estate registry (BACKLOG 6.14) — a consumer `install.sh`
installed into outside this directory is audited too.
