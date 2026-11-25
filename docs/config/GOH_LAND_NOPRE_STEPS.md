# GOH_LAND_NOPRE_STEPS
Area: pushes
Kind: setting
Order: 50
Default: none

The lint steps a `goh land --enqueue --land ... --no-pre REASON` runs beside `GOH_LAND_CHECK_STEPS`
(GOH.NOPREBATCH): `--no-pre` waives the pre-step's tests, never the lint it also ran. Each verbatim
one of `GOH_CI_STEPS`, run with `GOH_RUST_COVERAGE=defer`; goh declares `./gates/rust_gate.sh` (fmt,
clippy). A `--no-pre` land is never batched with another (`lib/landq_batch.py` `alone`).
