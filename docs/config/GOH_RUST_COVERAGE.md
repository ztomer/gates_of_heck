# GOH_RUST_COVERAGE
Area: coverage
Kind: setting
Order: 20
Default: unset

Environment, set by a repo's `tools/gate.sh` for its COMMIT gate: `defer` makes `rust_gate.sh` skip
the coverage step by name ("coverage deferred to the push gate") because the push gate checks the
floor; `plain`, set by `lib/land_gate.sh` for a land of a repo with `GOH_SHIP_FULL_SUITE=on`, runs
the coverage step's tests plain (cargo's and the external suite at once, the first red stopping the
other, GOH.RUSTOVERLAP) and measures nothing ("coverage: not measured at a land"), because
the ship's instrumented full run judges every floor over what landed since the last ship
(GOH.COVOFFLAND); any other value fails. A release touches every crate, and one cold instrumented
build per crate per commit was most of a 13-minute release.
