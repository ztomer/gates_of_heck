# GOH_BENCH_WAIT
Area: pushes
Kind: setting
Order: 720
Default: `1800`

Seconds `tools/quiet.sh` waits for another measurement, then for running gates, before it gives up
naming them. A drain is also bounded by the claim's budget (cap minus `--hold`): one still busy then
lets go and retries, since every new gate waits behind it.
