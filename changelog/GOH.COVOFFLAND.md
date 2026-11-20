* **Coverage is the ship's, not every land's** (GOH.COVOFFLAND): the instrumented coverage run was 69-76% of a goh land's
  gate (493-781 s; lands took 15-30 min end to end). A land of a repo whose ship makes the instrumented full run
  (`GOH_SHIP_FULL_SUITE=on`) now runs the coverage step's tests plain (`gates/rust_gate.sh` `GOH_RUST_COVERAGE=plain`,
  set by `lib/land_gate.sh`), so a red test is still red at the land. Every floor stays as strict: the ship's own run
  (`lib/ship_full_run.py`), never skipped now, judges the line floor and the diff coverage of every line landed since
  the last ship (`GOH_CHANGE_BASE` at the last version tag; it judged no base before). A red run releases nothing, and
  the ship's worker, with the land lock given back, bisects the lands since the last ship (`goh-landed`) to the first
  red one and files a fault item naming it (`bisect` in the ship result). Retired with it: the ship's skip on another
  run's record and its audited `fullrun` path, and the land's fast pre-step set for a gate that measures
  (`GOH_LAND_PRE_FAST`, `lib/pre_fast.py`, `affected_tests.py --fast-only`).
  A switch, `.gatesrc` `GOH_SHIP_COVERAGE=dummy` (off by default), measures coverage nowhere: while it stands every
  `goh ship` is refused, "coverage in dummy mode; no release"; deleting the line turns coverage back on.
