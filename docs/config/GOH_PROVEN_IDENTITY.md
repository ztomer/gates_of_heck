# GOH_PROVEN_IDENTITY
Area: runtime
Kind: internal
Order: 350
Default: set by `gates/_proven.sh`

The run-constant half of the proven cache's identity (PYTHONPATH's entries that are no gates root,
`lib/gate_identity.py proven`; the toolchains), asked once per process tree. The gates roots'
content is each step's closure line (`GOH_PROVEN_CLOSURES`). Not a setting.
