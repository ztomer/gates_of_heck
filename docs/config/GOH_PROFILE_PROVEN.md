# GOH_PROFILE_PROVEN
Area: runtime
Kind: setting
Order: 410
Default: unset

`tools/gate_profile.sh` only: keep the proven-step cache on while profiling (default off, because a
profile of cache hits measures the cache, not the work). `tools/land.sh` gives its gate `1` unless
set, so the push after a landing reuses the landed tree's proofs.
