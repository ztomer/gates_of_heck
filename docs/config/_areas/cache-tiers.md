# Cache tiers and eviction (`lib/cache_tier.py`, `lib/cache_sweep.py`, GOH.CACHEEVICT)
Order: 60
Level: 2

The removable large tier is resolved at use time and never written into static config; one
GDSF evictor ranks the cargo build dirs, the llvm-cov entries and the sccache bytes over one
budget per tier.

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->
