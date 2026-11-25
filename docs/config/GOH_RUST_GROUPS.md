# GOH_RUST_GROUPS
Area: local-ci
Kind: setting
Order: 120
Default: env, internal

Which of `rust_gate.sh`'s groups (`crate`, `repo`, `coverage`, comma-separated) a run proves; set by
`--each-crate` (`repo` once, `crate,coverage` per crate). Read from the ENVIRONMENT before
`.gatesrc`, so a repo file cannot narrow its own gate. An unknown word fails.
