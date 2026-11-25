# GOH_CONTAINER_STATE
Area: pushes
Kind: setting
Order: 760
Default: `~/.cache/goh/container_runtime`

State directory for the container runtime manager (`lib/container_runtime.sh`): leases, mkdir-lock,
and the start marker. Machine-wide on purpose; set it only to isolate a test.
