# GOH_COV_PART
Area: coverage
Kind: setting
Order: 60
Default: unset

rust only, set by hosted CI (`.github/workflows/gate.yml`, GOH.CIWALL): make ONE coverage part and
judge no floor -- `workspace` (cargo's own targets) or `K/N` (the external suite's K-th of N shards,
`part-external-K-of-N.info`). `rust_gate.sh` never proves or reuses a part. Unset: one run makes
every part and judges them.
