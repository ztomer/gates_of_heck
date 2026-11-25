# GOH_SHARE_DIV
Area: pushes
Kind: internal
Order: 680
Default: set by the coverage gate

How many test binaries `gates/_cov_run_tests.py` runs at once: the live runner (`lib/worker_share.py
run`, cargo's runner under an admission when the repo names none of its own) gives each `live // N`
test threads. Not a setting.
