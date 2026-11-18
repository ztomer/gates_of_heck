# D-0011 — an instrumented land does not select its binary-running tests by function hash

- **Question:** (GOH.COVSELECT, for GOH.LANDPROFILE) Rust coverage was 593 s of the 625 s land gate
  at v0.124.0, and same-tree verdict reuse never deselects a test that ran the native binary under
  coverage (`tests/_land_verdicts.py` `reusable`/`carried`): PROVENPYPATH, not rebased, still spent
  583.8 s / 3294 cpu-s in coverage. Can per-test coverage maps (each test's profile, naming the Rust
  functions it reaches) choose the binary-running tests a Rust diff can affect, with the floor
  judged on a complete profile whose other tests' counts are reused, keyed on the binary's
  unchanged functions?
- **Evidence:** (2026-11-17, probe in a lane worktree, instrumented `goh` built as the external
  part builds it, `profile.dev.opt-level=2`, 26 s)
  * The key is blind. rustc's coverage hash covers a function's own body and spans: a tiny program
    whose `fn a` reads `const LIMIT` kept every function hash when LIMIT went 5 -> 1, and the old
    profile, read against the new binary, credited `a`'s `else` (count 1) where the fresh run
    leaves it 0 and takes `then`: the floor counts a branch the binary no longer runs. A body change
    to `b` moved only `b`'s hash; two lines inserted above the functions moved all three, and the
    source path is in the hash too (`tests/test_cov_function_hash_blind.py` pins the first two).
  * goh's code is table-driven, so the blind case is the common one: of the 40 latest commits
    touching `crates/goh{,-sys,-tui}/src`, 25 (62%) change lines outside every function body
    (consts, static tables, struct/enum, `mod`), 12 change function bodies (plus `use`, comments,
    signatures) only, 3 only `use`/comment lines (a brace-depth classifier over `git diff -U0`).
  * The maps themselves are narrow and cheap: 80 nodes drawn (seed 7) from the 1811 of 6114 that
    ran the binary in the 2026-11-17 21:29 full carry record; 70 left a profile (98 raw files,
    36 MB). Per test: 23 ms to merge `--sparse` (1.61 s for 70), 19 KB stored (1.3 MB for 70);
    merging all 70 took 0.09 s. Functions reached per test: min 16, median 61, max 646 of 3846
    instrumented. Five files are reached by >= 99% of tests (`main.rs`, `house_cmds.rs`,
    `platform.rs`, `goh_dir.rs`, `ported.rs`), `goh-tui/src/lib.rs` 87%, `gitutil.rs` 83%.
  * File-level rerun share (tests reaching any changed file; the line-shift above makes a file the
    honest unit for a body edit): the 12 body-only commits median 10%, mean 30%, 3 of 12 at 100%.
  * 19 of the last 150 first-parent commits on main (13%) change the binary's build inputs
    (`lib/binary_runners.py` `build_input`); the other 87% leave the instrumented binary as it was.
- **Options priced:**
  1. Function-hash selection, as asked. The floor lies on a const or table change (proven above),
     62% of Rust commits are that shape. Rejected: the gate would report coverage it did not see.
  2. (1) behind a source fallback (any changed line outside a function body re-runs all).
     Honest on the cases above but still blind to method resolution (an inherent method shadowing
     a trait method changes callers whose bodies, and hashes, did not change); it helps the 12 of
     40 body-only Rust commits, about 5% of all commits (13% x 38%), median 90% of the binary
     tests each. Rejected for that return against a second change classifier.
  3. A per-function key over the compiled IR, its referenced globals' initializers followed (a
     const is an immediate, a table a global, a shadowing method another callee). Sound where (1)
     is blind; costs an `--emit=llvm-ir` of the binary crate and its parse per land, unmeasured.
     Not built; the re-open condition below.
  4. Same-binary profile carry: when the diff touches no build input (87% of commits) the
     instrumented binary is unchanged, so a binary-running node `tools/affected_tests.py` does not
     choose reuses its stored per-test profile exactly as its verdict is carried today (key: repo,
     tree, tools and the instrumented binary's content hash; one audited reuse path). Measured
     cost about 42 cpu-s and 34 MB per recorded tree (1811 x 23 ms, x 19 KB). Needs the external
     part's report to merge stored `.profdata` (`cargo llvm-cov report` reads only this run's
     `.profraw`) and, by the owner's terms (2026-11-17, "we don't want to completely lose
     coverage"): every land's floor on a complete merged profile; a fresh full instrumented run on
     main's tip before every ship, made the idle full run (`lib/idle_suite.py`) and required by
     the release's full-suite check (`lib/reuse.py full-suite`, `lib/release_gate.py`); that run
     the shadow of the reuse path, compared per crate by `lib/reuse_audit.py compare`, so
     `rustcov` is sampled and a disagreement turns the carry off. TAKEN as the follow-up, not
     built in this lane.
- **Ruling:** (GOH.COVSELECT, 2026-11-17) No function-level selection of binary-running tests
  under coverage: the coverage hash does not see what a body reads, and the floor would count
  branches the new binary never takes. Same-tree and carried verdict reuse keep re-running every
  binary-running node in the instrumented run. The speedup goes through option 4 under the
  owner's three terms, filed as its own item.
- **Falsifier:** `tests/test_cov_function_hash_blind.py`: a const change keeps the reader's hash
  and the reused profile credits the branch the fresh run leaves uncovered; a body change moves
  the hash (the instrument can see). Red when rustc's hash starts covering what a body reads.
- **Re-open when:** that test goes red (option 1 becomes honest), an IR-level per-function key is
  measured at under 30 s a land (option 3), or option 4 lands and its shadow disagrees.
- **Lives in:** `tests/test_cov_function_hash_blind.py`, `tests/_land_verdicts.py`,
  `gates/_coverage_rust.sh`
