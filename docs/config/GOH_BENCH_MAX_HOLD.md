# GOH_BENCH_MAX_HOLD
Area: pushes
Kind: setting
Order: 710
Default: `900`

Seconds a measurement may hold the host, counted from its claim -- the drain, the settle and the
run; every session's commits wait behind it -- before a waiting gate treats its claim as void and
proceeds. `tools/quiet.sh --hold S` (default two thirds of this) declares its run (stamped when it
starts, printed to a waiting gate), lets go when drain + settle + S would pass this, and refuses an
S longer than this: split it.
