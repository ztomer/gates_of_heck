# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-05, v0.22.0 (read first)

v0.22.0 shipped; details in CHANGELOG.md. The pytest hang is retired. Box: 16 cores, 4-5 busy
at idle, sys ~= user, so **spawn count, tree walks and network round trips are the cost metric, not
CPU**; `-n 12` is slower than `-n 8`.

**Landed in v0.21.0** (each red-first; details in its commit and the CHANGELOG stanza):

| item | commit | measured |
|---|---|---|
| C1 staged ruff check, both tiers | `f711a57` | pre-commit now refuses what pre-push refused |
| P0 `GOH_TIMINGS` + `tools/gate_profile.sh` | `79e1732` | media_server push profiled: 177 s, 297 steps |
| P1a manifest walks read git's listing | `38aea55` | `lints` 2.42 s -> 0.05 s native, 1.4 -> 0.08 s Python; `deps` 7 s -> 0.09 s on a small crate |
| P1b crates.io answers cached + concurrent | `fe354ac` | routines `deps` 0.97 s -> 0.12 s warm |
| P1c (gate side) failing test named under coverage | `a2bc0a6` | verified on real cargo-llvm-cov |
| P1d coverage builds once, profile reset per target | `7d7bd8c` | mediaops-rs 60 -> 27 s, routines 144 -> 73 s, merged report identical |
| P1e native delegated checkers start together | `8124ce4` | media_server `--full` 4.75 -> 2.4 s |
| C3 bin/goh from HEAD, source-stamped, rebuilt when stale | `833b2e7` | -- |
| `gates/required_tools.{tsv,py}` (antiknob CI ask) | `54e2ee7` | manifest == code, both directions |
| push_gate refuses a branch that moved while gated (ZoneWM D-0211) | `e148449` | -- |
| P3 rust_gate groups proven on their own inputs (`goh rust-scope`) | `750a114` | see the v0.21.0 stanza |
| `required_tools --names`, ruff its own layer (divoom CI) | `3a9b465` | -- |

**Landed in v0.22.0:**

| item | commit | measured |
|---|---|---|
| C5 failure block quotes only the step that failed (ZoneWM H2; `lib/fail_lines.py`) | `24eb31d` | nested: innermost dump alone, others counted; `make`: its failing target first, matches flagged |
| P2 `GOH_CI_JOBS` scheduler: declared-order reports, `[tag]` exclusion | `3044941` | gates_of_heck's own push steps, warm, proven off: 1 job 186 s, 2 jobs 147 s, 4 jobs 138 s (box busy with other runs: provisional) |
| a stopped gate stops its steps (bounded_run + local_ci forward TERM/INT/HUP) | `efbaf12` | -- |
| P1g `GOH_RUST_LINT_CARGO=cargo-zigbuild` for `--target` lint configs (+ the first tests of `GOH_RUST_LINT_CONFIGS`) | `89b7c9f`, `f39fed9` | media_server http-mini-rs: plain cargo fails on ring's C, zigbuild 6 s warm |
| P2 `rust_gate.sh --each-crate` + P1f repo scans once (`GOH_RUST_GROUPS`, `GOH_RUST_JOBS`) | `17e1541` | media_server clone, warm, 4 at a time, proven off: 79 s vs the `xargs -P 4` fan-out's 91 s; repo scans 1 run vs 29 (cold: 104 s, all 29 green). Adoption is servers' own commit |
| C4 consumers run the gates from an immutable export of HEAD; `GOH_LIVE=1` is the working tree on purpose | `c327d66` | +45 ms per gate run (antiknob pre-commit 0.44 vs 0.40 s); a planted uncommitted edit reached the consumer 0 times, 1 with GOH_LIVE |

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

| what | baseline | now | target |
|---|---|---|---|
| media_server push, one crate changed, warm | 197 s | re-measure (P1a/b/d landed) | <= 30 s (needs P3) |
| media_server push, everything changed, warm | 177 s (P0) | re-measure | <= 90 s |
| any consumer's pre-commit structural layer | ~1 s | re-measure | <= 0.4 s |
| `structural.sh --full`, media_server | 4.9 s | 2.4 s (P1e); 1.9 s (N1 ports); **0.30 s warm** (P4 shell-lint cache) | <= 1.5 s -- MET |
| this repo's suite, `-n 8` | 94.4 s | re-measure | <= 60 s |

### P0 — the instrument -- LANDED `79e1732`

`GOH_TIME` prints whole seconds on the ok line, too coarse for anything under 1 s.
- `goh_step` and `local_ci.sh` append one JSON line per step (label, ms, exit, cache hit or miss)
  to `$GOH_TIMINGS` when it is set. `push_gate.sh` sets it inside the export and names the file.
- `tools/profiling/gate_profile.sh <repo>` runs the push gate on HEAD (nothing pushed) and prints
  the top steps by wall time and the sum per class (fixed overhead / tests / lint / scan).
- **Exit:** the table above reproduced from the instrument rather than by hand, for
  media_server, routines, ztools and monitor. Red-first: a step that sleeps 300 ms reads 300 ms
  +/- 50, not 0 or 1 s.

### P1 — remove waste (no cache, so nothing can lie; do first)

- **P1a. LANDED `38aea55` for the Cargo.toml walkers** (the rest are allowlisted with reasons in `tests/test_tree_walks.py`). **Tree walks read the index, never the filesystem.** `rglob`/`os.walk`/`glob("**")` in
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
- **P1b. LANDED `fe354ac`. Dependency currency: one lookup per crate NAME per TTL, concurrently.** A shared response
  cache under `~/.cache/goh/crates-io/<name>.json`, honouring the index's own `ETag`/`max-age`;
  lookups in a thread pool. **Why caching cannot turn a red green:** the fatal arm (pinned below
  the graph) is offline and never touches the cache; only the report-only arm reads it. Test the
  claim, don't just state it: the fatal arm must go red with the network seam disabled.
  Expected: 6-7 s -> ~0.1 s warm, in every Rust crate of every consumer.
- **P1c. GATE SIDE LANDED `a2bc0a6`; the consumer half is routines', ztools' and monitor's own commit.** **Every test runs once per push, the expensive run included.** Reordering alone cannot do
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
- **P1d. LANDED `7d7bd8c` (the per-target recompile; the per-binary-export idea is unneeded: 2x already, merged report identical).** **Incremental coverage.** `cargo llvm-cov clean --workspace` exists because the export
  globbed STALE instrumented binaries from a shared build dir (routines, 2026-09-21: 96% read as
  93.5%). The root-cause fix is to export only from the executables cargo reports for THIS run
  (`--message-format=json`, `profile.test` artifacts). Then the clean can go and instrumented builds
  become incremental. Then measure one test run plus a per-binary `llvm-cov export` against today's
  N `cargo llvm-cov` invocations. **Parity:** the merged lcov must be byte-identical to today's on
  media_server, routines and app_updates. The phantom-miss reason for per-target parts (CGU-hash
  instantiations) must survive, so its existing test is the pin. Expected: `mediaops-rs` coverage
  53 s -> to be measured, but it is the largest single lever in the table.
- **P1e. NATIVE TIER LANDED `8124ce4`; the Python tier is left serial on purpose -- it is being retired (N).** **Run checkers concurrently, with output in declared order.** The native tier
  runs its 7 delegated Python steps in a pool (`steps_delegated.rs`); `structural.sh` does the same
  for its Python steps (buffer each, print in order). The parity test already pins order, so it is
  the gate. Expected: `--full` 4.9 s -> ~2.3 s (bounded by unreaped-spawn).

- **P1f. Two per-crate steps that are not per-crate** (measured by P0 on media_server's push):
  `no emptiness asserts` scans the WHOLE repo (554 files) once per crate, 29 times, 24 job-s; and
  `cargo lints (manifest)` is a second full `cargo check --workspace` after clippy, 24 job-s. The
  first belongs in P2's `--each-crate` (repo-wide scans once per run). The second is NOT redundant,
  checked 2026-10-05: it deliberately builds without `--all-targets`, so a `[dependencies]` entry
  only tests use is flagged -- clippy's all-targets run counts it as used (routines shipped exactly
  that, `gates/rust_manifest_gate.sh` header). It stays.

### P2 — a scheduler for the step list -- LANDED (see the table); what is left

- **The budget curve, on a quiet box.** The one sweep (table) ran beside other suites. Its shape
  already says the critical path: gates_of_heck's pytest step is ~96 s of 186, so 4 jobs is bounded
  near 140. Re-sweep 1/2/4/6 quiet, and on routines (a repo whose steps are closer in size).
- **Opt-ins, each the repo's own commit:** media_server's `xargs -P 4` -> `rust_gate.sh
  --each-crate` (servers); `GOH_CI_JOBS` with `[cargo]` tags in four repos before any default moves.
- **Lies tested** (tests/test_local_ci_jobs.py, test_bounded_run_interrupt.py): order, tag
  exclusion, accumulator, a timeout reaping only its own group, stdin EOF, an inherited
  `GOH_CI_JOBS`/`GOH_CI_STEPS` ignored, TERM/INT/HUP reaching every running step. Not tested by
  construction: two UNTAGGED steps writing one file -- that is what declaring a tag is for.

### P3 — input-scoped proven cache -- LANDED `750a114` (rust_gate's crate/repo/coverage groups)

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
- **Per-blob verdict cache -- LANDED for shell lint** (`crates/goh/src/verdict_cache.rs`,
  `GOH_VERDICT_CACHE` / `GOH_VERDICT_DIR`): media_server `structural --full` 1.9 -> 0.30 s warm.
  Every key input red-proven (`tests/test_shell_lint_verdict_cache.py`: an edit of the same size,
  a `.shellcheckrc`, `SHELLCHECK_OPTS`, the shellcheck binary, a corrupt record, the switch).
  The other pure-per-file checkers were already native and cost 10-45 ms each; re-open when one
  measures over 100 ms. The original plan, kept for that day:
  **Per-blob verdict cache (`GOH_CACHE`)** for checkers that are pure per file (emoji, conflict
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
- **C3. LANDED `833b2e7`** (SUPERSOTA R4a): `bin/goh` is built from an export of HEAD, stamped with
  the git trees of its inputs (`goh source-tree`), and `gates/_goh_bin.sh` rebuilds a stale one
  under a lock before using it. Owner's choice 2026-10-05: rebuild, not fall back.
- **C4 residuals -- CLOSED.** Every Python script entry point runs HEAD's copy when called from
  the shared checkout (`gates/_from_head.py`, pinned by `test_python_from_head.py`, a planted edit
  reaching a direct call 0 times); `goh.sh`, `rust_each_crate.sh`, `rust_manifest_gate.sh` and the
  consumer-facing tools (`gate_profile.sh`, `release-kit/*.sh`) gained the bash trampoline, whose
  root lookup now walks up (release-kit sits two levels down). What still reads the live tree, by
  design: a consumer's `PYTHONPATH=$GOH_DIR` IMPORT of a house lib (`tui.lib`, `mcp_scaffold`) --
  an import cannot re-exec its importer; the fix there is the consumer pinning a version.

## Phase N — retire the Python checkers (owner's direction, 2026-10-05)

The Python checkers are today both the second tier (`GOH_NO_NATIVE`) and the SPEC the native ports
are pinned to. Retiring them means porting the delegated ones and then deleting the tier, in that
order, never the reverse:

- **N1. Port the delegated checkers, slowest first** (P0 numbers). **`check_no_unreaped_spawn.py`
  LANDED** as `goh unreaped-spawn` (`crates/goh/src/unreaped/`): every table row's whole verdict
  list equal through both (`test_unreaped_spawn_native_parity.py`), the four whole-repo test files
  run on both tiers, the report byte-identical on 30 local repos; media_server 2.87 s -> 0.13 s.
  Found while porting and FIXED in both tiers with red rows (the table is 59 shapes now):
  `RETURNS` matched `-> ()` with a space, and the shell masker blanked a quoted heredoc tag before
  reading it, so `<<'EOF'` bodies were scanned as code (and `<<<` opened a heredoc). **`check_version_provenance.py` LANDED** as
  `goh version-provenance` (`crates/goh/src/provenance.rs`; tests on both tiers via
  `tests/tier_kit.py`, byte-identical on every local repo at all three output modes).
  **`check_no_kill_by_name` LANDED** as `goh kill-by-name` (`crates/goh/src/killname/`): a Python
  lexer replaces `ast`+`tokenize` for the comment/docstring strip; code lines identical on 1,767
  `.py` files across 30 repos, reports identical at both scopes. Its one possible divergence is a
  file that TOKENIZES but does not PARSE (the reference falls back to plain lines; the port cannot
  see a parse error) -- none in the estate. **`check_claim_derivation` LANDED** as
  `goh claim-derivation` (`crates/goh/src/claims/`, on the shared `crates/goh/src/pylex/`,
  which kill-by-name now uses too): byte-identical on every local repo and 61 fixture cases. Two
  reference bugs fixed in the Python with red tests (a module-level list holding a non-literal
  crashed the gate with AttributeError; `ast.parse` leaked another file's SyntaxWarning into the
  report), and -- reproduced first, then fixed in both -- `_python_prose` blanked the rest of the
  line a multi-line string ends on. **`check_md_links` LANDED** as
  `goh md-links` (`crates/goh/src/mdlinks.rs` + `mdtext.rs`): anchors and links identical on 627
  `.md` files across 30 repos, reports identical at three modes. **`check_lock_version` LANDED**
  as `goh lock-version` (`crates/goh/src/lockver.rs`, the version-source registry in
  `versrc.rs` for the tag gate's port to share): identical on every local repo under five source
  specs; the reference died with a traceback (exit 1, read as findings) on an unknown source kind
  -- fixed, red first, exit 2. **`check_no_credential_urls` LANDED** as `goh credential-urls`
  (`crates/goh/src/credurls/`; the "delegated on purpose" note on it is superseded by this
  phase): the measured URL table splits the same through `urlsplit` and the port, reports are
  byte-identical on every local repo and on a repo holding every table row.
  **`check_python_formatted` LANDED** as `goh python-formatted` (`crates/goh/src/pyformat.rs`):
  it spawns ruff itself, so `crates/goh/src/bounded.rs` gives native spawns what `bounded_run`
  gives delegated ones -- a ceiling, a process-group kill, bounded pipe drains (a kill of the
  leader alone hung the run on its child's pipe, measured). Identical on every local repo at
  both scopes. **`check_shell_lint.sh` LANDED** as `goh shell-lint` (`crates/goh/src/shell_lint.rs`;
  `required_tools`' site scan now reads the native `on_path("tool")` shape too). The native
  structural tier delegates nothing per file any more -- only the two `--full` sweeps.
  **`check_dep_currency` LANDED** as `goh deps` (`crates/goh/src/deps/`; `toml` with preserve_order
  for document order, the shared crates.io cache, `curl` for the network): identical on every local
  Rust repo in four modes and over the live network. Two reference bugs fixed first, red first:
  `--ratchet` crashed (`f["name"]` on a dataclass) instead of failing an untriaged major, and the
  comparator read a missing minor/patch as 0 (`~1.0`, `=1.2`, `^0.0.3`, `^0.0` -- five rows of
  Cargo's own table). The semver table now lives in the crate's tests (N2 for this checker). Next: `check_md_links`, `check_python_formatted` (spawns ruff either way),
  `check_lock_version`, `check_no_credential_urls`, `check_shell_lint.sh` (spawns shellcheck), and
  the gate-side Python (`check_dep_currency`, `check_lints_optin`'s twin, `lcov_merge`). Each port
  lands parity-pinned against the Python it replaces and red-proven both ways, as Phase 3 did.
- **N2. Re-home the spec.** A parity test whose reference is deleted pins nothing. Before a Python
  checker goes, its behaviour table moves into the native crate's own tests (the calibration tables
  stay whole -- they are the spec, not the implementation).
- **N3. Delete the tier.** `GOH_NO_NATIVE`, the Python branch of `structural.sh`, the fallback in
  `_goh_bin.sh` (a failed rebuild then REFUSES, which is correct once there is nothing else),
  `docs/config.md` rows, and the "both tiers" tests that become one-tier tests.
- **Cost of the end state to say plainly:** every consumer then needs a Rust toolchain to rebuild
  `bin/goh`; `gates/required_tools.tsv` gains `cargo` for the structural layer.

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

- **antiknob / divoom CI:** install from `python3 $GOH_DIR/gates/required_tools.py --repo . --install`
  once gates_of_heck is pushed (the divoom/antiknob session has asked to switch).
- **Every consumer pushing by branch name over HTTPS:** `push_gate.sh` now refuses a ref that moved
  while it was gated; ZoneWM's pinned `git push <remote> <sha>:<branch>` is the airtight form.

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
