* The goh binary's source stamp names every file the build embeds (GOH.STAMPINPUTS). It was the git
  trees of a hand list of four roots (`crates`, `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`),
  and the binary `include_str!`s `lib/busyloop_shapes.json` and `gates/required_tools.tsv` from
  outside them: an edit to either alone left the stamp, so the store served the stale build and the
  gates judged with the old busy-loop shapes or tool manifest. `lib/goh_version.sh` now derives the
  inputs (`goh_stamp_inputs_at`, `--inputs`) from the crates' own `include_str!`/`include_bytes!`/
  `include!` literals at the commit, test modules (`tests.rs`, `*_tests.rs`) left out; `build.rs`
  asks the script for the inputs and the stamp (one formula), and `build-goh.sh`, the `GOH_LIVE`
  delta and `lib/gate_identity.py` read the same list. `goh-tui`'s build script embeds
  `tui/stylerc` instead of reading it, so it is an input too. `tests/test_stamp_inputs.py` holds the
  derivation to the tree: no embedded file unstamped, every test-named module under `cfg(test)`,
  and no build script reading a file at run time.
