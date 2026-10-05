# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-05, v0.20.0 (read first)

v0.20.0 shipped green; details in CHANGELOG.md. The pytest hang is retired (full suite completes,
94.4 s at `-n 8`). Box: 16 cores, 4-5 busy at idle, sys ~= user, so **spawn count, tree walks and
network round trips are the cost metric, not CPU**; `-n 12` is slower than `-n 8`.

## Phase P — make goh fast (the program)

### Where the time goes, measured 2026-10-05

| measurement | wall | breakdown |
|---|---|---|
| media_server pre-push, warm | 197 s | 29 independent crates, `rust_gate.sh` each, 4 at a time (`tools/gate.sh --full`, hand-rolled `xargs -P 4`) |
| rust gate, `mediaops-rs` (largest crate) | 69 s | **coverage 53 s**, lints-inherited 6 s, dependency currency 6 s, fmt+clippy+rest ~4 s |
| rust gate, `time-mini-rs` (small crate) | 14.6 s | **dependency currency 7 s, lints-inherited 3 s**, coverage 2 s, the rest ~1 s |
| `goh.sh lints` on `mediaops-rs` | 2.4 s, **sys 2.27 s** | `rglob("Cargo.toml")` scans 50,950 directories, nearly all in `target/` (452 MB) |
| `structural.sh --full`, media_server | 4.9 s | unreaped-spawn 2.2 s, version provenance 1.4 s, 6 others 0.1-0.35 s, **run serially** |
| bare `python3 -c pass` | 0.044 s | x ~20 Python checkers per structural run |
| this repo's suite, `-n 8` | 94.4 s | long poles: `test_goh_structural_parity` (22 full cases x 2 tiers), end-to-end gate tests |

(The two crate rows ran while another gate was active on the box; P0 re-measures them clean.)

What the table says, in four classes:

1. **Fixed per-crate overhead that is not about the crate.** In a small crate, 10 of 14.6 s go to
   two steps: one walks into `target/`, the other sends one uncached, serial HTTPS request per
   dependency for a **report-only** arm (`checks/_crates_io.py` `latest_stable`). Across 29 crates
   that is ~280 job-seconds of media_server's ~790.
2. **Rust tests run twice per push** where a repo declares both `cargo test` and
   `coverage_gate.sh` (routines, ztools, monitor). A green instrumented run already proves the
   tests pass. Nothing records that, and the proven cache keys on step strings, which differ.
   monitor runs them a third time in its pre-commit hook.
3. **Serial where independent.** `local_ci.sh` runs steps one by one. `structural.sh` runs its
   checkers one by one, in both tiers. The coverage gate makes one `cargo llvm-cov` invocation per
   test target, after `cargo llvm-cov clean --workspace` forces an instrumented rebuild of every
   member on every run.
4. **Whole-tree cache keys.** The proven-step cache (`gates/_proven.sh`) keys on the whole tree, so
   a one-line README edit re-gates all 29 crates. Most pushes touch one crate.

### Targets (each re-measured with P0, recorded here)

| what | now | target |
|---|---|---|
| media_server push, one crate changed, warm | 197 s | <= 30 s |
| media_server push, everything changed, warm | 197 s | <= 90 s |
| any consumer's pre-commit structural layer | ~1 s | <= 0.4 s |
| `structural.sh --full`, media_server | 4.9 s | <= 1.5 s |
| this repo's suite, `-n 8` | 94.4 s | <= 60 s |

### P0 — the instrument (prerequisite, small)

`GOH_TIME` prints whole seconds on the ok line, too coarse for anything under 1 s.
- `goh_step` and `local_ci.sh` append one JSON line per step (label, ms, exit, cache hit or miss)
  to `$GOH_TIMINGS` when it is set. `push_gate.sh` sets it inside the export and names the file.
- `tools/profiling/gate_profile.sh <repo>` runs the push gate on HEAD (nothing pushed) and prints
  the top steps by wall time and the sum per class (fixed overhead / tests / lint / scan).
- **Exit:** the table above reproduced from the instrument rather than by hand, for
  media_server, routines, ztools and monitor. Red-first: a step that sleeps 300 ms reads 300 ms
  +/- 50, not 0 or 1 s.

### P1 — remove waste (no cache, so nothing can lie; do first)

- **P1a. Tree walks read the index, never the filesystem.** `rglob`/`os.walk`/`glob("**")` in
  `checks/_dep_tree.py`, `_rust_crates.py`, `_empty_scope_probe.py`, `check_empty_scope.py`,
  `check_lints_optin.py`, `check_no_screen_presentation.py`, `check_python_formatted.py`,
  `check_tests_registered.py`, `gates/coverage_engines.py`, `coverage_markers.py`; Rust
  `lints.rs`, `screen.rs`, `skills.rs`, `skills_audit.rs` (the last two read `~/.claude/skills`,
  which is not a git tree: they get an explicit prune list instead). Index truth is already the
  house rule (AGENTS.md: staged checks read the index); walking the disk was also *wrong*, because
  a `Cargo.toml` vendored into `target/` by a build script counts as a manifest. **Class gate:** a
  fixture with a 5,000-directory ignored `target/` and a stray `Cargo.toml` inside it, run against
  every walking checker: the stray file is never reported, and walk time stays under a fixed bound.
  Expected: lints-inherited 3-6 s -> < 0.1 s per crate.
- **P1b. Dependency currency: one lookup per crate NAME per day, concurrently.** A shared response
  cache under `~/.cache/goh/crates-io/<name>.json`, honouring the index's own `ETag`/`max-age`;
  lookups in a thread pool. **Why caching cannot turn a red green:** the fatal arm (pinned below
  the graph) is offline and never touches the cache; only the report-only arm reads it. Test the
  claim, don't just state it: the fatal arm must go red with the network seam disabled.
  Expected: 6-7 s -> ~0.1 s warm, in every Rust crate of every consumer.
- **P1c. Every test runs once per push, the expensive run included.** Reordering alone cannot do
  it: the plain `cargo test` is a separate step and re-runs whatever ran before it. What can: the
  instrumented coverage run executes every `lib`/`bin`/`test` target with `--all-features`, which is
  everything `cargo test --all-features` runs EXCEPT doctests (and examples/benches, inventoried per
  repo before claiming the set is complete). So the house rust gate owns ONE test step, in this
  order: cheap fail-fast lints first (fmt, clippy, no-allow: a red one stops before any test
  compiles), then the instrumented run as THE test run, then `cargo test --doc` for the remainder.
  The plain run is the cheaper of the two, so this keeps the expensive one, once. A seam to
  consider and reject: coverage writing a proven record for the `cargo test` step string. It would
  certify doctests it never ran. Red-first: a failing `#[test]` must turn `coverage_gate.sh` red
  **naming the test**, not only "export failed". Today a failure only reaches the gate through a
  missing `.ok` marker; prove that path before relying on it. Then routines, ztools and monitor drop
  their plain `cargo test` step, each repo's own commit. ACROSS hooks, the same test still runs in
  pre-commit and again at push (monitor); that duplicate is P3's to remove (a pre-commit record on
  the same crate scope satisfies the push). Expected: one full test run saved per push in those
  three repos.
- **P1d. Incremental coverage.** `cargo llvm-cov clean --workspace` exists because the export
  globbed STALE instrumented binaries from a shared build dir (routines, 2026-09-21: 96% read as
  93.5%). The root-cause fix is to export only from the executables cargo reports for THIS run
  (`--message-format=json`, `profile.test` artifacts). Then the clean can go and instrumented builds
  become incremental. Then measure one test run plus a per-binary `llvm-cov export` against today's
  N `cargo llvm-cov` invocations. **Parity:** the merged lcov must be byte-identical to today's on
  media_server, routines and app_updates. The phantom-miss reason for per-target parts (CGU-hash
  instantiations) must survive, so its existing test is the pin. Expected: `mediaops-rs` coverage
  53 s -> to be measured, but it is the largest single lever in the table.
- **P1e. Run checkers concurrently, in both tiers, with output in declared order.** The native tier
  runs its 7 delegated Python steps in a pool (`steps_delegated.rs`); `structural.sh` does the same
  for its Python steps (buffer each, print in order). The parity test already pins order, so it is
  the gate. Expected: `--full` 4.9 s -> ~2.3 s (bounded by unreaped-spawn).

### P2 — a scheduler for the step list

`local_ci.sh` runs `GOH_CI_STEPS` concurrently under a job budget, each step's output buffered and
printed in declared order, the fail accumulator and exit codes unchanged.
- Safety by declaration, not inference: a step may carry a resource tag (`cargo:<dir>`,
  `screen`, `net`), and steps sharing one never overlap. Untagged steps run concurrently only when
  the repo opts in (`GOH_CI_JOBS`, documented in `docs/config.md`); the default stays serial
  until P0 shows the opt-in green in four repos. Cargo's own build lock already serializes steps
  sharing a target dir correctly; the tag is there to stop them burning budget while blocked.
- The job budget defaults to a measured value, not `nproc`: on this box more workers were slower.
  Sweep 2/4/6/8 with P0 and record the curve here.
- media_server's hand-rolled `xargs -P 4` over crates becomes a goh feature
  (`rust_gate.sh --each-crate`), so its 29-crate fan-out uses the same budget and P3's per-crate
  keys.
- **Lies to test first:** two steps writing the same file (the Finance `*.out` class); a
  step that reads the terminal; a timeout that must kill only its own group (`bounded_run.py`
  already does; prove it under concurrency).

### P3 — input-scoped proven cache (the "common caching")

Extend `gates/_proven.sh`: a step may declare its INPUT SCOPE, and its key uses the git tree
objects of those paths instead of the whole tree. A tree sha per path costs one `git rev-parse
HEAD:<path>`, so computing a key stays O(scope), not O(files).
- **Rust per crate:** scope = the package dir + its path-dependency closure (`cargo metadata`) +
  the lockfile + the workspace manifest + `.cargo/` + `rust-toolchain*` + **every tracked file
  outside any package** (scripts, fixtures, config). The only assumption is "a crate does not read
  inside a sibling package it does not depend on", and that assumption is checked, not trusted (below).
- **Pre-commit records count at push.** The pre-commit rust gate (coverage deferred) records
  per-crate keys on the index tree, and the push finds them for the steps they cover.
- **Every way a hit can lie, each a test before the cache code** (extends the GOH_CACHE list that
  was designed and never built):
  1. compile-time reads outside scope (`include_str!("../../x")`, `build.rs`): after a green run,
     every path in rustc's dep-info (`*.d`) must lie inside the scope, or **nothing is recorded**
     and the miss is named. The cache checks itself.
  2. runtime reads outside scope (a test opening `../other-crate/fixture`): a static scan of the
     crate's test sources for sibling-package paths refuses a scoped key for that crate (whole-tree
     key instead); the residual (a path built at runtime) is stated here, not hidden.
  3. a config key or env var the step reads that the key omits: `GOH_PROVEN_ENV` exists; add the
     step's `.gatesrc` keys to the key wholesale.
  4. a cached green over ZERO files: the empty-scope refusal must hold on a hit.
  5. gate source changed: already in the key (goh HEAD + diff + untracked). Keep the test.
  6. toolchain moved: already in the key. Add `cargo-llvm-cov` and `cargo-machete` versions.
- Expected: media_server, one crate changed: 197 s -> that crate's gate + `repo_gates.sh`,
  target <= 30 s.

### P4 — the scanners, after P1e

- **One interpreter for the Python checkers.** `python3 -m checks.run` imports each checker module
  and calls its `main(argv)` in-process, with stdout captured per checker and `SystemExit` caught.
  That removes ~20 interpreter starts (~44 ms bare, more with imports) per structural run. The
  parity table is the pin; a checker that keeps module-global state is a finding to fix there.
- **Per-blob verdict cache (`GOH_CACHE`)** for checkers that are pure per file (emoji, conflict
  markers, secrets, home paths, version provenance). Key = checker source hash + its config keys +
  blob sha; value = findings. Its re-open condition ("a run dominated by scan time") is now met at
  full scope: 3.6 of 4.9 s in media_server. **Not** for unreaped-spawn, whose guard types are judged
  at crate scope. Its rule needs crate-level keys, or a native port first: it is the 2.2 s pole.
  The four lies listed under P3 apply unchanged.

### P5 — this repo's suite

- `test_goh_structural_parity`: one structural run per (fixture, tier), shared by every assertion
  on it (module-scoped fixture), fixtures built from one template copy (the `_display_seam_probe`
  pattern). Measure the case count x run time first.
- Re-time the SERIAL suite once (last on record ~12 min; never re-measured since `-n 8`).
- The suite is this repo's own push gate. Once P3 exists, `checks/` and `crates/goh/` get scoped
  keys too, but the full suite still runs whenever a gate source file changes. That is the product.

## Correctness items that come before speed

- **C1. Pre-commit is weaker than pre-push for a cheap check.** `python is ruff-formatted` runs at
  full scope only; two unformatted test files passed pre-commit and refused the v0.20.0 push. Add a
  staged mode to `check_python_formatted.py` (index blobs via `--stdin-filename`; it reformats
  nothing), wire it in both tiers (`steps_delegated.rs::step_python_formatted`), re-pin parity.
- **C2. The skills corpus has no gate at the moment of WRITING.** This repo's push reads
  `~/.claude/skills` (`GOH_SKILLS_CORPUS`); a peer's edit tipped a skill over the word ceiling and
  refused a release. Home: a hook or a check the skill-editing path runs, so the writer finds out,
  not the next pusher.
- **C3. SUPERSOTA R4a.** The binary's version check sees a version bump, not a step added. Close it
  with a source hash built into `goh` (`crates/goh/build.rs`). P4's checker-source hash needs the
  same thing, so build it once.
- **C4. Uncommitted gate source certifies a push with only a warning.** A refusal needs a seam the
  tests can set (an acknowledged-dirty marker file; a `GOH_*` key would need a `docs/config.md` row).

## Downstream: what each consumer session needs to know

- **Every repo:** re-run `$GOH_DIR/install.sh <repo>`; `structural.sh` names a hook that is an older
  stock (14 repos were on the pre-`0ac0f70` pre-push, which never reaches `push_gate.sh`).
- **Every Rust repo:** a stale `Cargo.lock` is RED; missing `cargo-machete`/`swiftlint`/`shellcheck`
  is RED up front.
- **routines, ztools, monitor:** after P1c lands, drop the plain `cargo test` step (keep `--doc`).
- **media_server:** after P2, `tools/gate.sh --full` replaces its `xargs -P 4` loop with
  `rust_gate.sh --each-crate`.
- **antiknob / divoom:** `tools/lock_guard.sh` is redundant with the gate; retire it there.
- **ztools:** HEAD (`40148ae`) is RED on its own code under clippy 1.99 (72 `assert_is_empty`). Its
  hooks differ textually: `install.sh --force` is ztools' call. Write `GOH_EXCLUDE='^vendor/'`; the
  key stays a `re.search` substring test (decided 2026-10-05: anchoring would un-exempt everyone).
- **monitor:** customised pre-commit (`check_gate_parity.py`), pre-push still the working-tree
  generation; convergence is monitor's call. Its pre-commit runs the full test suite: P3 lets the
  push reuse that run instead of repeating it.
- **ZoneWM:** build files are under the line cap in both tiers; `check_probes_pass.py` discovery
  stays by `check_*` NAME (`input_lock.py --probe` would grab the real keyboard) and names the 23
  self-proofs it does not run.
- **Finance:** `tests/test_repos.sh:113`, `tests/test_one_plan_of_record.sh:214` write
  `"$root.out"` beside the repo root; `push_gate.sh` removes and names it; the fix is Finance's.
- **Over the line cap since build files entered scope:** `CadGoose/CMakeLists.txt` (702),
  `games/CadGoose2/CMakeLists.txt` (1142), `games/necrohand/Makefile` (511).
- **games/ZeroThunder:** `tests/e2e/garden_drag_flicker.py:73` drops its process handle on the
  `wait_for_app` failure path (`return 2` at :76, `terminate()` at :119); the gate is red on it.
  Unfixed because it needs a real screen to prove.
- **O35 (servers):** a per-consumer `gate_calibration.json` registry; a feature here, unscoped.

## Blocked / owner decisions

- O33 (servers): the `gho_` PAT was never rotated; an older `ghp_` is still live in `.90`'s zsh
  history and three conversation DBs. Only the owner can rotate it.
- Wording for "everyone owns gates_of_heck" and the `:latest` image policy in `~/.claude/CLAUDE.md`.

## Open — residuals, stated so they are not rediscovered

### `check_no_unreaped_spawn.py` + `lib/orphan_canary.py`

- A child that calls `setsid()` leaves the step's process group, the canary's only evidence
  (`ppid == 1` cross-reported every concurrent gate). Needs its own signal, e.g. a helper pidfile.
- Per-function, not interprocedural: a spawn RETURNED is a handoff (four in the estate). Same-file
  callees that can panic are read as panicking (v0.18.0); other files and crates are not.
- Cross-CRATE guard types are reported `unjudgeable`, counted and named, never failed on.
- A kill inside a conditional (`if cond { child.kill(); }`) is accepted. Cost unknown; needs a corpus.
- Rust, Python, shell only. No `.swift`/`.ts`/`.go` test spawns in the estate today; re-measure.
- Ten PEP 604 annotations in `tests/*.py` without `from __future__ import annotations`. Harmless
  under the session interpreter; no gate forbids it because `pyproject.toml` enables the ruff
  formatter only, by decision.

### SUPERSOTA residuals (`docs/SUPERSOTA.md` §3)

- R3: `check_estate_corpus.py` proves a checker still REFUSES a plant; it cannot prove the checker
  is correct, and consumer-side checks are unmeasured.
- R5: nothing refuses a future vendored copy. Home: `gates/structural.sh`.
- `tests/test_gate_environment.py` duplicates `_hermetic_env`; its home is `tests/conftest.py`.

### Deferred, each with its re-open condition

- Secrets v2 (entropy): measure the FP rate on all consumer trees before enforcing.
- shfmt: only as reformat-everything, or a flag set proven clean on this tree.
- Swift coverage merge: needs a real-swift oracle across Xcode versions.
- Periodic disk watch: `scripts/bin/disk_hygiene.sh --warn-only` wants a launchd plist.
- `secret-ok` scope: line + line-above; path-scoped rules only under real fixture pressure.
- `lcov_merge` native port: re-open when a merge measures > 1 s (largest today: 76 ms).
- `check_tests_registered.py` / `check_generated_fresh.py` stay Python: no shared gate runs them.
- Credentials: `.netrc` out of scope until some gate already reads outside its repo; CI configs are
  committed, so `check_no_secrets.py` covers them. Not a gap.

## Done (prune to history)

- v0.20.0 (2026-10-05): per-step 0.2 s poll, probe git spawns, serial self-proofs and sweep (suite
  109 -> 94 s); parse guards; tools and `Cargo.lock` as hard inputs; push gate run ownership, stable
  export path, reaper; build files under the cap; credential helpers and headers; stale hooks
  self-report. The estate leak table (routines, monitor, ZeroThunder `live_probe_lib.py` fixed) is
  in git history at `adf2efd`.
- v0.19.0: R3 sweep scoped to this repo; `_spawn_rust.py` quadratic (media_server 23.2 -> 2.1 s).
- Rust-port arc (2026-09-23): shared tree walk, one shellcheck call, native ports parity-pinned,
  staged blobs read once (197 -> 15 git spawns on a 50-file commit).
- v0.8.0 arc: disk watch out of CI, pooled du, parallel suite, shell lint, coverage floor, secrets
  v1, swift parity, doctor, timeouts, timing, xdist grouping.
