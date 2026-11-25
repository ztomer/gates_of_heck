# GOH_LAND_HOLD_FD
Area: pushes
Kind: setting
Order: 220
Default: unset

A pipe's file descriptor: `tools/land.sh` runs SPECULATIVELY -- it never rebases its branch (main
left its base: exit 3), and a green gate waits on that pipe for the word `go` before main moves;
anything else, or EOF, lands nothing, exit 3. A red batch gates its second half on main beside the
first this way, and lands it with no further gate when the first lands nothing (GOH.LANDBISECTPAR,
`lib/landq_spec.py`). Never in the gate's environment. Internal: the lander sets it.
