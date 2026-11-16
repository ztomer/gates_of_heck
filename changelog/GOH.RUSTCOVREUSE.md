* The Rust coverage measurement, and every other Rust proven group, is now found again from the
  next land's push worktree (GOH.RUSTCOVREUSE). The first land after GOH.RUSTCOVSKIP measured for
  535.36 s of rust_gate's 564.40 s on a tests-only diff: each group's step named a root workspace by
  its absolute path (`~/.cache/goh/push/wt-<hash>/wt`, new per lane) and hashed per-push values
  (`GOH_CHANGE_BASE`, the two push marks, the tip's head export as `GOH_DIR`), so no record was ever
  found from one land to the next. The workspace is named repo-relative (`.` at the root);
  `GOH_STRUCTURAL_MARK` and `GOH_CI_STEPS_MARK` are verdict-free; `GOH_CHANGE_BASE` leaves rust_gate's
  environment and reaches crates-behind alone; `GOH_DIR` is keyed by its scripts' content. The
  measurement's key now also holds the coverage configuration `.gatesrc` sets (every `GOH_COV_*`
  but the cache's place and size): a raised floor reused the old measurement, and the plain run
  judges no floor. Every run prints its decision as a `↔` line a passing step shows: reused and
  from when, or measuring and why (cache off, dirty tree, no identity, no record yet, other inputs,
  another configuration, no plain build). The external suite stays outside the inputs (the owner's
  set): a test change re-measures at the next Rust-input change.
