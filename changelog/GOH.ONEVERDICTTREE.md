* The Rust coverage measurement keys the GOH_* it can read, by value, never the whole
  configuration (GOH.ONEVERDICTTREE, lib/cov_env.py): the names the keyed files' bytes spell (the
  Rust build inputs, the coverage scripts, the suite's runner; a name ending in `_` is a prefix)
  and the suite's declared inputs (tests/suite_home.py CALLER_INPUTS, the only GOH_* the suite
  keeps). The land's own bookkeeping no longer voids the release push's reuse (9 of the last 9
  releases re-measured, 380-980 s each), and a miss names the variable that moved. A repo whose
  suite declares nothing keys every GOH_* as before. docs/reuse.md maps the eight stages a goh
  change is verified in, with their keys and measured seconds.
* The proven cache's own settings (GOH_PROVEN*) are no input of the coverage measurement's key
  (GOH.ONEVERDICTTREE): the first land under the new key recorded `goh-env GOH_PROVEN=1`, which
  tools/gate_profile.sh sets for the land and the release push leaves unset; GOH_PROVEN=0 still
  makes no key at all.
