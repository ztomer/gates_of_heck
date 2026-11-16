* A goh land or push whose tree changes none of the Rust coverage measurement's inputs reuses that
  measurement and runs the tests plain (GOH.RUSTCOVSKIP). With an external suite
  (`GOH_COV_RUST_EXTERNAL`: goh's pytest against an instrumented `goh`) the coverage group keyed on
  the whole tree, so every land re-measured: 896.62 s of GOH.XEXITDETAIL's 21:37 land, for Python
  and shell. The measurement is now recorded under `lib/gate_identity.py rust`: the repo's Rust build
  inputs (the stamp formula, embedded files included), the Rust gate's coverage scripts (read from
  what `gates/coverage_gate.sh` sources, imports and runs, plus `rust_gate.sh` and
  `_rust_proven.sh`) and the tools. On a hit `cargo test --tests` and the external suite run against
  the plain build of those same inputs, so a red test is still red; a Rust-input change, a workspace
  member outside the keyed inputs, a binary that is not this tree's build, a dirty tree or
  `GOH_PROVEN=0` measures in full. A Python test change does not re-measure the Rust coverage number
  until the next Rust change (the owner's input set).
