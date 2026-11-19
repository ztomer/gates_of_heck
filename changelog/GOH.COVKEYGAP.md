* The Rust coverage measurement's key holds every file gates/rust_gate.sh sources or runs, derived
  from the sources (GOH.COVKEYGAP, lib/rust_cov_scripts.py): its `scripts` line hashed two seeds
  and gates/coverage_gate.sh's closure, so 13 files the gate SOURCES (gates/_proven.sh,
  gates/_hash.sh, gates/_common.sh, whose goh_step judges the coverage step through lib/xexit.py)
  and 36 with what they run were outside it, and an edit to one reused a measurement another
  program made. A `source` or run the reading cannot place is declared with its reason or the
  measurement has no key and runs; tests/test_cov_key_closure.py reads every run, source and
  script name of the closure on its own and fails on a miss or a stale entry. Over the last 300
  goh commits, 122 move the key where 113 did.
