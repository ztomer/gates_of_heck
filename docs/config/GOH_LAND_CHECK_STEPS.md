# GOH_LAND_CHECK_STEPS
Area: pushes
Kind: setting
Order: 40
Default: none

The land-gate steps `goh land --check` runs (and every `goh land --enqueue --land` before its
pre-step), colon-separated like `GOH_CI_STEPS` and each verbatim one of them, the environment, else
the repo's `.gatesrc`; run in the checkout beside the declared guard tests (`lib/land_check.py`,
GOH.LANDCHECK). goh declares the structural gate, `status-markers`, `shape-diff`, `py_staged.sh
--all`, the lock order and the saved-pid probes. None declared: only the guard tests run.
