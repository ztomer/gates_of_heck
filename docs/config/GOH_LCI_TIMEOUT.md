# GOH_LCI_TIMEOUT
Area: local-ci
Kind: setting
Order: 130
Default: **900**

Per-step wall-clock ceiling in seconds. Expired steps are TERM-then-KILLed **in their own process
group** — the whole subtree, not just direct children — and fail named with exit 124 plus the pids
that were still running. Non-numeric values are exit-2 usage errors. `0` opts out and must be typed.
Was `unset` = no limit, which meant a hung `cargo test` produced no observable at all: the run that
would have reported it was the run that had been killed (a private server workspace, 2026-10-03).
