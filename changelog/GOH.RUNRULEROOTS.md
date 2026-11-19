* A script a gate runs by `$PROVEN_GOH_ROOT/...`, or by a variable it assigns from its own
  directory, is an edge of the proven step closure (GOH.RUNRULEROOTS, lib/run_rule.py): the run
  rule followed only $HERE, $_goh_d, $GOH_ROOT, $GOH_DIR and $GOH, so lib/gate_identity.py,
  lib/step_closure.py, lib/kept_rewrite.py and lib/reuse_audit.py (run by gates/_proven.sh and its
  siblings) were outside every proven step's key. The Rust coverage key places a script path only
  through a declared root: one through any other variable is declared with its reason or refuses
  the key (LookupError, the measurement runs), as is a declared root naming no file. A consumer's
  GOH_COV_RUST_EXTERNAL runner that sources or runs goh files by `$GOH_DIR` keys them on a
  `runner-goh` line.
