# GOH_ROUND_PREFLIGHT
Area: pushes
Kind: setting
Order: 800
Default: (unset)

A command `round.sh` runs (`bash -c`, repo root) before the commit; non-zero stops the round with
nothing committed. Point it at the repo's warm verify (e.g. `make verify`) so a red test fails in
seconds, not after the push's cold gate.
