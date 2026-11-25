# GOH_REMOTE_DIR
Area: remote-worker
Kind: setting
Order: 20
Default: `/mnt/scratch/goh-worker`

The host directory that is the container's only mount: `trees/<slot>/` (one checkout per lane slot,
keyed by the Mac checkout's path), `cache/`, `out/`. A cache: wiped, the next run rebuilds it ("cold
cache").
