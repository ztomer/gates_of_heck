# GOH_RUST_JOBS
Area: local-ci
Kind: setting
Order: 110
Default: **4**

Crates gated at once by `rust_gate.sh --each-crate` (through `local_ci.sh`'s scheduler). Non-numeric
or `0` is a config error.
