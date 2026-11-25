# GOH_COV_FLOOR_RUST, GOH_COV_FLOOR_SWIFT, GOH_COV_FLOOR_CPP, GOH_COV_FLOOR_PY
Area: coverage
Kind: setting
Order: 10
Default: unset

Per-language floor. Resolution: `--floor` flag, then this key, else exit 2 naming both seams.
`rust_gate.sh` passes its floor as explicit argv when set (`.gatesrc` values are not exported);
`coverage_gate.sh` also honors them from the environment on direct invocation.
