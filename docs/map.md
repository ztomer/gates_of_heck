# docs/map.md — script inventory

Every entry: what it does, when it runs, which test pins it.

## Gates (`gates/` — bash runners, sourced via `_common.sh`)

| Script | Purpose | Invoked | Test |
|---|---|---|---|
| `_from_head.sh` | Sourced FIRST by every entry point (C4): re-runs the gate from an immutable export of the gates checkout's HEAD (`~/.cache/goh/head/<sha>`, `GOH_HEAD_CACHE`), so an uncommitted edit in the shared checkout judges no consumer; `GOH_LIVE=1` runs the working tree on purpose. | every `gates/*.sh` entry point | `test_gates_from_head.py` |
| `_from_head.py` | The Python entry points' half of C4: every script entry point (`checks/check_*.py`, `gates/*.py`, `lib/*.py` run as a script) starts with a stanza that, run from the shared CHECKOUT, execs HEAD's copy from the export `_from_head.sh` builds -- a direct `python3 "$GOH_DIR/checks/check_no_emoji.py"` judged with uncommitted edits before. One stat when already the export; `GOH_LIVE=1` runs the tree. | every Python entry point | `test_python_from_head.py` |
| `_head_cache.py` | Prunes the HEAD export cache whenever `_from_head.sh` makes a new export: by LAST USE (each use truncates the export's `.goh-used`), unused 7 days, or beyond the 20 most recently used and idle an hour; an export used within the hour is never removed. | `_from_head.sh` | `test_head_export_prune.py` |
| `verdict_free_keys.txt` | The GOH_* keys that never change a verdict (bookkeeping, the timing instrument): `goh_config_hash` (`_hash.sh`) and `checks/_estate_cache.py` leave exactly these out of a cache key. Pinned to config.md's "Not a setting" rows. | `_hash.sh`, `_estate_cache.py` | `test_verdict_free_keys.py` |
| `_hash.sh` | Sourced: the ONE sha256 of an installed hook, so the record `install.sh` writes and the comparison `doctor.sh` makes cannot drift. | `install.sh`, `doctor.sh` | `test_install.py` |
| `rust_manifest_gate.sh` | Cargo's OWN lints, as a step separate from clippy: built without `--all-targets`, so a `[dependencies]` entry only tests use is flagged (clippy's all-targets run counts it as used). | `rust_gate.sh` | `test_rust_gate.py` |
| `swift_toolchain.sh` | Sourced: resolves THE `swift` a house script builds with, once (`goh_swift_resolve` sets `SWIFT` and its provenance). | `swift_gate.sh`, `coverage_gate.sh` | `test_swift_toolchain.py` |
| `coverage_engines.py` | The two coverage ENGINES that produce a report; `coverage_swift.py` judges it. Split out at the line cap. | `coverage_swift.py` | `test_tree_walks.py` |
| `coverage_markers.py` | `cov:ignore` marker handling for the Swift coverage gate: which source lines the repo has excluded, and why. | `coverage_swift.py` | `test_tree_walks.py` |
| `_common.sh` | Shared contract: fail-fast, print output, TUI, EXIT-trap sentinel, and a per-step ceiling (`GOH_STEP_TIMEOUT`, 1800s by default, printed on every step line) enforced through `lib/bounded_run.py`. `goh_step` had NO timeout while `local_ci.sh` did, so the same hang was bounded in one path and unbounded in the other. Sourced, never run. | every gate | `test_goh_init_trap.py` |
| `_goh_bin.sh` | The ONE resolution of the native `goh` binary (`GOH_BIN` trusted or reported, then `bin/goh`, then `PATH`; `GOH_NO_NATIVE` is retired and only named). Sourced, never run. | `structural.sh`, `goh.sh` | `test_goh_entry.py` |
| `goh.sh` | `goh.sh <check> [args]`: the one way a consumer runs a house checker — native, same arguments; with no binary it refuses (exit 2). Only a flag the native lacks (`--probe`, `--selftest`, `--fresh-derivations`) still runs the Python reference, and says so. Consumers call this, never `checks/*.py` or `bin/goh` directly. | `rust_gate.sh`, consumer repos | `test_goh_entry.py` |
| `structural.sh` | Layer 1, every repo. `20` steps in `tests/test_goh_structural.py:INVENTORY` (a marked claim, re-derived on every push), measured by running it: emoji, conflict markers, markdown-link resolution, Cargo.lock/manifest agreement, the ruff format check (opt-in), the file-length cap, the skills corpus (opt-in), shell lint, committed secrets, home paths (opt-in), kill-by-name (opt-in), unreaped spawns in tests, prose-claim derivation (opt-in), no vendored copies of house checkers, and -- at full scope only -- the empty-tree refusal and the gate self-proofs. Version provenance runs when the repo ships `.gates-version-baseline.json`. The row used to name eight of them, which is the class `check_claim_derivation.py` exists for: a stale inventory in the one table whose whole job is being the inventory. Execs the native `bin/goh` (`scripts/build-goh.sh`), the only tier since Phase N3; with no binary it refuses. `--staged` = pre-commit. (Disk hygiene was removed from this gate in v0.8.0 and from this repo in v0.8.1 — it lives in `~/Projects/scripts`.) | `tools/gate.sh`, hooks | `test_gates_e2e.py`, `test_file_length_and_markers.py` |
| `py_gate.sh` | `ruff check` + `ruff format --check` + pytest with coverage floor. Args: `[repo] [pkg_dir]`. | `--full`, opt-in | `test_cwd_and_py_gate.py` |
| `py_staged.sh` | `ruff check` + `ruff format --check` on the STAGED `*.py` only; the cheap half of `py_gate.sh` at commit time. Args: `[repo]`. | every commit, opt-in | `test_py_staged.py` |
| `rust_gate.sh` | `cargo fmt --check` + `clippy -D warnings` + `check_no_allow` + `check_no_empty_assert` + coverage floor when `GOH_COV_FLOOR_RUST`/`GOH_COV_FLOORS_JSON` is set (via `coverage_gate.sh --lang rust`, explicit argv). Args: `[repo] [cargo_dir]`. | `--full`, opt-in | `test_rust_gate.py` |
| `swift_gate.sh` | swiftlint (+ optional baseline ratchet) + cold build + test + coverage floor. `spm` or `xcode` mode. | `--full`, opt-in | `test_swift_gate_baseline.py`, `test_swift_gate_project_selection.py` |
| `swift_lint_baseline.py` | Helper: reconciles swiftlint JSON report against baseline (shrink-only). Called by `swift_gate.sh`, not directly. | via swift gate | `test_swift_gate_baseline.py` |
| `coverage_gate.sh` | ONE parameterized coverage gate: `--lang rust\|swift\|cpp\|py --floor N [--ignore RE] [--include RE] [--floors-json P] [--marker-ceiling P] [--engine E] [path]`. Floor: flag, then `GOH_COV_FLOOR_<LANG>`, else exit 2. Rust mode lives in the sourced `_coverage_rust.sh` (line cap): built once, profile reset per target, one lcov part per target. | opt-in | `test_coverage_gate.py`, `test_coverage_strictest.py`, `test_coverage_rust_exports.py`, `test_coverage_gate_cpp_parse.py` |
| `coverage_swift.py` | Helper: swift coverage engine behind `coverage_gate.sh --lang swift` (llvm-cov export / xccov). | via coverage gate | `test_coverage_swift_selection.py` |
| `lcov_merge.py` | Helper: merges per-target lcov exports, strips CGU hashes. Called by coverage gate rust path. | via coverage gate | `test_lcov_merge.py` |
| `rust_each_crate.sh` | `rust_gate.sh --each-crate [repo]`: the house Rust gate over EVERY tracked top-level crate (a nested testkit manifest is its crate's business), biggest first, `GOH_RUST_JOBS` at a time, through `local_ci.sh`'s scheduler -- and the repo-wide scans ONCE (`GOH_RUST_GROUPS=repo`), not once per crate. Replaces media_server's hand-rolled `xargs -P 4`. | opt-in | `test_rust_each_crate.py` |
| `local_ci.sh` | Declarative step runner: steps from `GOH_CI_STEPS` + `--step`. Fail accumulator, logs on failure, and a per-step ceiling that is ON BY DEFAULT (`GOH_LCI_TIMEOUT`, 900s; `0` opts out). Each step runs through `lib/bounded_run.py` in its own process group, and each step is bracketed by `lib/orphan_canary.py`, so a child the step leaked fails the step that leaked it. Every step goes through the proven-step cache. | opt-in | `test_local_ci.py`, `test_proven.py` |
| `proven.sh` | `proven.sh [--label L] [--log F] -- '<step>'`: run one step unless that exact step string already passed on this exact clean tree (same gates, toolchains, keyed env) within `GOH_PROVEN_TTL_S`. For a repo hook whose step also appears in `GOH_CI_STEPS`, so the push-time run is a hit. | repo hooks | `test_proven.py` |
| `_proven.sh` | The proven-step cache itself (key, lookup, record, prune), sourced by `proven.sh` and `local_ci.sh` so the key a hook records and the key local CI looks up cannot drift. Sourced, never run. | via the two above | `test_proven.py` |
| `_rust_proven.sh` | `rust_gate.sh`'s proven cache, sourced: the crate, repo and coverage groups each proven once on their own inputs. Crate scope from `goh rust-scope` (the workspace + its `path =` packages + the config they read; a runtime `../` widens it to the tree), and a green group is recorded only if the compiler's dep-info read nothing outside it. | `rust_gate.sh` | `test_rust_gate_scoped_cache.py` |
| `required_tools.tsv` + `required_tools.py` | The ONE list of tools a gate refuses without (one row per refusal site), and the reader CI workflows install from: `gates/required_tools.py --repo . --install` prints `brew install ...` / `cargo install --locked ...` for the layers the repo declares. A hand-kept workflow list drifted the day v0.20.0 made shellcheck a hard requirement (antiknob CI). | CI workflows | `test_required_tools_manifest.py` |
| `push_gate.sh` | The pre-push gate every installed hook delegates to: refuses a release tag its own commit does not declare (`goh tag-version`, over the refs git hands the hook) BEFORE any worktree is made, then runs `tools/gate.sh --full` in a clean export of each pushed commit, skips a commit the remote already has, and refuses a branch that moved while it was gated. | `hooks/pre-push` | `test_push_gate.py`, `test_check_tag_version_push_gate.py`, `test_proven.py` |
| `doctor.sh` | Wiring diagnosis for one repo: GOH resolution, hooksPath, unknown `.gatesrc` keys (derived from `config.md`), toolchains. Exit 0 healthy / 1 problems named. | `gate.sh --doctor` | `test_doctor.py` |
Which coverage file to touch: `coverage_gate.sh` = CLI/floor plumbing for
all languages; `coverage_swift.py` = swift engine only;
`checks/check_swift_coverage.py` = legacy swift-gate helper used by
`swift_gate.sh` (not by `coverage_gate.sh`); `lcov_merge.py` = rust lcov
merge only. The two swift implementations measure differently (codecov-JSON
aggregation vs llvm-cov line level) and are NOT merged — instead
`tests/test_swift_coverage_parity.py` runs both against one 50% fixture and
pins identical verdicts, so arithmetic drift goes red. New per-language
logic goes in the engine; new floor/CLI semantics go in `coverage_gate.sh`.

## Native checks (`bin/goh`, run as `gates/goh.sh <check>`)

Every structural checker is native since Phase N3; the Python checkers they port are retired, and their measured behaviour is the frozen spec the parity suites run (`tests/reference_kit.py`, pinned to commit `96018bd`). `goh <check> --help` is each one's usage.

| Check | Source | Purpose | Test |
|---|---|---|---|
| `goh deps` | `crates/goh/src/deps/` | Dependency currency: a direct dependency pinned below the version a transitive parent resolves is FATAL (offline, never cached); one behind crates.io's latest is reported (cached per crate name, asked concurrently). | `test_check_dep_currency.py`, `test_crates_io_cache.py` |
| (structural step) | `crates/goh/src/vendored.rs` | No vendored copy of a house checker (SUPERSOTA R5): a file named after one of `$GOH_DIR/checks/*` or a retired checker is REFUSED by the commit that adds it, and named (not failed) at full scope. `gates_of_heck` itself is exempt. | `test_vendored_copies.py` |
| `goh ceiling` | `crates/goh/src/ceiling.rs` | Every file exempted from the line cap (`GOH_LINE_EXCLUDE`) must still have a CEILING in the baseline, so an exemption is a bound and not a blank cheque. | `test_check_exclusion_has_ceiling.py`, `test_goh_ceiling_parity.py` |
| `goh lints` | `crates/goh/src/lints.rs` | A crate silently exempt from its workspace's lint policy (no `[lints] workspace = true`). | `test_check_lints_optin.py`, `test_goh_lints_parity.py` |
| `goh home-paths` | `crates/goh/src/homepaths.rs` | A tracked file hard-coding a path under someone's HOME (`/Users/<name>/`, `~/Projects/`); opt-in `GOH_NO_HOME_PATHS`. | `test_check_no_home_paths.py`, `test_goh_homepaths_parity.py` |
| `goh python-formatted` | `crates/goh/src/pyformat.rs` | The repo's Python is `ruff format`-clean by its own declared settings, at full scope or over the staged blobs; a missing ruff is a missing gate. | `test_check_python_formatted.py` (both tiers) |
| `goh skills` | `crates/goh/src/skills.rs` | Structural gate for an agent SKILLS corpus (`~/.claude/skills`): frontmatter, word ceilings, dead cross-links -- code nothing compiles, so every defect is silent. | `test_check_skills_corpus.py`, `test_goh_skills_parity.py` |
| `goh emoji` | `crates/goh/src/emoji.rs` | Emoji policy gate (allow-list in `ALLOWED_ORDERED`). | `test_check_no_emoji.py` |
| `goh markers` | `crates/goh/src/markers.rs` | Fails on merge markers. | `test_file_length_and_markers.py` |
| `goh length` | `crates/goh/src/length.rs` | `--max N` file-length cap. | `test_file_length_and_markers.py` |
| `goh shell-lint` | `crates/goh/src/shell_lint.rs` | `bash -n` + `shellcheck --severity=error` over tracked `*.sh` + `hooks/*`. | `test_check_shell_lint.py` |
| `goh secrets` | `crates/goh/src/secrets.rs` | Narrow secrets gate: known key prefixes + private-key headers + a credential-named key (`api_key`, `password`, …) with a 32+ char quoted value, staged + full. | `test_check_no_secrets.py` |
| `goh credential-urls` | `crates/goh/src/credurls/` | A credential in a **git remote URL** — the class `check_no_secrets.py` cannot reach, because `.git/config` is untracked by definition. | `test_check_no_credential_urls.py` |
| `goh no-allow` | `crates/goh/src/noallow.rs` | No `#[allow]` in Rust (repo-local twin; structural twin lives in consumer `tools/`). | `test_check_no_allow.py` |
| `goh empty-assert` | `crates/goh/src/emptyassert.rs` | No `assert!(x.is_empty())` / `x.len() == 0` in `assert!`, where clippy's own suggestions (`assert_eq!(x.len(), 0)`, `assert_ne!`) are left alone. | `test_check_no_empty_assert.py` |
| `goh unreaped-spawn` | `crates/goh/src/unreaped/` | A TEST that spawns a child nothing reaps **on the panic path**. | `test_check_no_unreaped_spawn.py`, `test_no_unreaped_spawn_estate.py` (both tiers), `test_unreaped_spawn_native_parity.py` |
| `goh kill-by-name` | `crates/goh/src/killname/` | A process killed BY NAME (`pkill`/`killall`, or a `pgrep`/`pidof` feeding a `kill` on the line): a name is not an owner (zinc, 2026-09-23: another session's browser SIGKILLed mid-reply). | `test_check_no_kill_by_name.py` (both tiers), `test_kill_by_name_native_parity.py` |
| `goh screen` | `crates/goh/src/screen.rs` | Static half: test sources must not ask for screen APIs. | `test_screen_presentation.py` |
| `goh lock-version` | `crates/goh/src/lockver.rs` | A committed `Cargo.lock` must agree with the manifests it was generated from: every workspace member's lockfile version against its own manifest (with `version.workspace = true` RESOLVED, never compared as the literal `true`), plus the declared release version where a source OUTSIDE the manifests declares one. | `test_check_lock_version.py` |
| `goh md-links` | `crates/goh/src/mdlinks.rs` | Every relative markdown link resolves to an existing file AND an existing anchor: GitHub heading slugs with duplicate counters, explicit `{#id}`, `<a id=…>`. | `test_check_md_links.py` |
| `goh claim-derivation` | `crates/goh/src/claims/` | A MARKED number in prose, re-derived from the tree: three adversarial reviews of the games estate converged on this class (2026-10) — `roadmap_state.py` "64 declared gates" against 70, `check_mcp_server.py` "477 lines and 18 tests" for 321 and 20, `gaf/README.md` "86 files / 1059 tests" for 101 / 1732, all found by hand at hours each. | `test_check_claim_derivation.py` |
| `goh tag-version` | `crates/goh/src/tagver.rs` | A pushed `refs/tags/v<semver>` must name the version its OWN COMMIT declares (never the working tree). | `test_check_tag_version.py`, `test_check_tag_version_push_gate.py` |
| `goh version-provenance` | `crates/goh/src/provenance.rs` | A `--version` flag carrying no commit. | `test_check_version_provenance.py` |

## Checks (`checks/` — called by gates)

| Script | Purpose | Test |
|---|---|---|
| `check_empty_scope.py` | The `--full` sweep: runs every gate over a skeleton with no CONTENT and fails any that passes -- a gate that reports success over nothing is the class. Delegated by the native tier. | `test_gate_runtime_path.py`, the empty-tree rows of each checker's own suite |
| `check_python_formatted.py` | Forwarder (Phase N3): execs `gates/goh.sh python-formatted` with the same arguments, for a consumer that calls it by path; imported, it raises. | `test_retired_shims.py` |
| `check_no_emoji.py` | Forwarder (Phase N3): execs `gates/goh.sh emoji` with the same arguments, for a consumer that calls it by path; imported, it raises. | `test_retired_shims.py` |
| `check_file_length.py` | Forwarder (Phase N3): execs `gates/goh.sh length` with the same arguments, for a consumer that calls it by path; imported, it raises. | `test_retired_shims.py` |
| `check_no_allow.py` | Forwarder (Phase N3): execs `gates/goh.sh no-allow` with the same arguments, for a consumer that calls it by path; imported, it raises. | `test_retired_shims.py` |
| `check_display_seam.py` | The OTHER screen gate, and not a copy of the one above: that one refuses screen APIs in TEST TARGETS, this one refuses them in APP SOURCE unless they route through a seam the consumer's JSON policy names. Input is the same problem as presentation (a real pointer read, an event tap, a cursor warp all compete with the user for their own mouse), so both share `swift_code_only`. A live-tier harness must CALL the policy's helper with a substantive, non-placeholder reason, and the executor may sit on an earlier line than the command — the shape `ruff format` emits. Every tracked `*.sh` is scanned whatever `scan` says. A policy that will not parse, names a key this checker does not know, names a seam that does not exist, or matches zero source is exit 2, never a pass. `--probe` proves every rule red-first. Came from `games/ZeroThunder`'s repo-local gate (SUPERSOTA R5/R6). | `test_display_seam.py` |
| `_marker_reason.py` | Lib: an escape-hatch marker must carry a real justification — a bare `// screen-ok:` silences nothing. Moved here from `games/ZeroThunder/tools/marker_reason.py`, where eight of that repo's gates imported it, rather than copied for the seam gate. Gates INTENT, not EFFECT: a fabricated reason is the same KIND of sentence as a true one. | `test_display_seam.py`, `test_marker_reason.py` |
| `_ordered_pool.py` | Lib: run a function over items concurrently and replay each call's printed output in item order (a per-thread stream; `redirect_stdout` is process-global). Used by the estate sweep: 9.4 s -> 5.2 s, output byte-identical to serial. | `test_ordered_pool.py` |
| `_display_seam_probe.py` | Lib: the self-proof for `check_display_seam.py`, split out so neither file crowds the 500-line cap. One case per pattern in the Swift and live-command vocabularies, plus the config refusals. | `test_display_seam.py` |
| `check_no_screen_linkage.sh` | Dynamic half: `nm -u` on built test binary must not import screen symbols. | `test_screen_linkage.py` |
| `check_swift_coverage.py` | Legacy helper for `swift_gate.sh` (SPM codecov JSON + xcresult walk). | `test_swift_coverage.py` |
| `check_swift_warnings.py` | One `swift build --build-tests`; any warning in the repo's own dirs fails, colours and OSC 8 hyperlinks stripped first (for repos that judge build output instead of `-warnings-as-errors`). | `test_check_swift_warnings.py` |
| `check_baseline_ratchet.py` | Shrink-only ceilings (JSON or line baselines). | `test_check_baseline_ratchet.py` |
| `check_generated_fresh.py` | Artifact freshness: regenerate to sandbox, hash-compare. | `test_check_generated_fresh.py` |
| `check_tag_version.py` | Forwarder (Phase N3): execs `gates/goh.sh tag-version` with the same arguments, for a consumer that calls it by path; imported, it raises. | `test_retired_shims.py` |
| `check_version_provenance.py` | Forwarder (Phase N3): execs `gates/goh.sh version-provenance` with the same arguments, for a consumer that calls it by path; imported, it raises. | `test_retired_shims.py` |
| `check_tests_registered.py` | Every test source must be registered in a build block. | `test_test_registration.py` |
| `_gitutil.py` | Lib: repo root, NUL-delimited file lists, index bytes via `git show :path`, `foreign_repo_env()` for git on any repo but the gated one. | `test_gitutil_paths.py`, `test_hook_git_env.py` |
| `check_estate_corpus.py` | SUPERSOTA R3: every declared house checker, handed a REAL subtree copied out of a real consumer repo with one violation planted inside it, must still go red and name the planted file. Six entries, each with the corpus and the reason it was chosen — the `check_no_empty_assert` entry replays the 2026-10-02 receiver bug in the exact shape it failed in (a string literal INSIDE the receiver). A corpus below a file floor, a corpus that is not CLEAN under the checker before the plant lands (so a red cannot be credited to the plant), and a plant that would land in a stub are all refusals. Copies, never runs in place: these are other people's working trees. `--probe` proves a checker that cannot fail is caught. | `test_estate_corpus.py` |
| `_estate_cache.py` | `check_estate_corpus.py`'s memory of VERIFIED entries, keyed on the entry, every corpus file's path/size/mtime, the checker's source, the native binary and the GOH_* environment; red and unavailable entries are never recorded. | `check_estate_corpus.py` | `test_estate_corpus_cache.py` |
| `_calibration.py` | Lib: reads `checks/gate_calibration.json` and holds every claim to the estate -- the key names a gate that exists, a cited prover resolves, is COMMITTED, and either ran a self-proof green in this sweep or (outside this repo) lists the key among what it proves. Called by `check_probes_pass.py`, which is what runs in every repo, so the registry is read on every gate run and a consumer repo with no registry is reported rather than failed. | `test_gate_calibration.py` |
| `_calibration_probe.py` | Lib: the self-proof for `_calibration.py`, split out so neither file crowds the 500-line cap. Runs from `check_probes_pass.py --probe`; asserts every way the registry can lie goes red and the sound registry comes back clean. | `test_gate_calibration.py` |
| `_retired.py` | `NATIVE` (every retired checker -> its `goh` check, spelled once; read by `_calibration.py` and the suite's `run_check`), `SHIMS` (the forwarders kept on disk) and `forward()`. | `test_retired_shims.py` |
| `_swift_text.py` | Lib: Swift source with comments and string literals masked, line count preserved -- the one Swift reader the remaining Python gates share (`check_display_seam.py`). | `test_display_seam.py` |

## Lib (`lib/`)

| Module | Purpose | Test |
| `swift_coverage_scope.py` | What counts as "the code under test" in a Swift coverage measurement -- ONE definition both measurement paths import. | `test_coverage_swift_scope.py` |
| `tree_stamp.py` | Did the working tree move while a gate ran over it? A stamp taken before and after; a gate certifying bytes that changed under it says so (ZoneWM 2c.9). | `test_tree_stamp.py` |
| `bounded_run.py` | Run one command under a wall-clock CEILING, in its OWN process group, and sweep the whole subtree on expiry (TERM, grace, KILL) — then print `TIMED OUT after Ns` and return 124 with the surviving pids named. It WAITS rather than polls (a 0.2 s poll charged every step of every gate up to 200 ms) and samples the process table once at exit. The group discipline is the measured part: `gates/local_ci.sh`'s old sweep was `pkill -P "$pid"`, DIRECT children only, which against `bash -c 'sleep 400 & wait'` left 2 grandchildren ALIVE — i.e. the mechanism meant to unstick a hung step left running exactly the orphans that make the NEXT step hang. `lib/killtree.py` already knew the answer for the Python callers; this exposes it to the shell gates. | `test_bounded_run.py` |
| `fail_lines.py` | The failure block `goh_step` prints before a red step's tail: only lines ATTRIBUTABLE to the sub-step that failed. A nested goh_step's own dump is quoted alone (the innermost, named) and the other matching lines are COUNTED, never listed -- a red `make verify-clean` had listed a calibration plant's quoted `✗` row, from a recipe that exited 0, as its failure (ZoneWM H2, BACKLOG C5). An unframed log (one `make` over many recipes) says so, and puts make's own `*** [target] Error N` first. | `test_goh_step_fail_lines.py` |
| `step_timings.py` | The P0 perf instrument: `record()` appends one JSON line per step to `$GOH_TIMINGS` (ms, rc, tier, parent label, cwd), called by `bounded_run.py` and, for its in-process steps, by the native tier (`step_report.rs`); `report FILE` prints the slowest steps and each label summed across every place it ran. | `test_step_timings.py`, `test_goh_structural.py` |
| `orphan_canary.py` | Before/after diff of the live process table, so a child a suite LEAKED is reported instead of inferred from the next run's hang — the case a ceiling cannot see, because the step exits 0 and the server outlives it. Attributable when the command line names this repo (checkout, a `target/` under it, `$CARGO_TARGET_DIR`) or when the pid is ORPHANED (`ppid 1`, measured). Everything else is counted in one line and never failed on: on a shared machine another program's processes are noise, and a canary that cries wolf is one nobody leaves switched on. | `test_orphan_canary.py` |
|---|---|---|
| `golden_core.py` | Pixel-diff math only (no render, no bless, no `--update`). | `test_golden_core.py`, `test_golden_core_cli.py` |
| `mcp_scaffold.py` | stdio MCP JSON-RPC scaffold (framing, dispatch, serve). Re-exports `mcp_schema` names so old import paths keep working. | `test_mcp_scaffold.py`, `test_mcp_scaffold_wire.py` |
| `mcp_schema.py` | JSON-Schema validation split out of the scaffold (pure, no I/O). | `test_mcp_scaffold.py`, `test_mcp_scaffold_wire.py` |
| `eval_transport.py` | Grader/model-agnostic eval transport. | `test_eval_transport.py`, `test_eval_transport_sweep.py` |
| `headless_env.sh` | `GOH_HEADLESS=1` contract; `headless_require_live` exits 3 when enforced. | `test_screen_presentation.py` |
| `killtree.py` | Kill whole process groups on timeout. | `test_killtree.py` |
| `desktop_lock/` | Machine-wide desktop mutex. | `test_desktop_lock.py` |

## Tools

* `tools/gate.sh` — this repo's own gate entry (layer 1 + parallel pytest
  on `--full`: `-n 12 --dist loadgroup`, serial fallback without xdist).
* `tools/release-kit/` — `release.sh` (gate → stanza → tag → push → release),
  `gen_app_icons.py`, `update_dev.sh`. Pinned by `test_release_kit.py` (+
  `test_release_hardening.py`, `test_profiling_scripts.py` for profiling).
* `tools/gate_profile.sh <repo>` — profile a repo's push gate on HEAD (nothing pushed) with `GOH_TIMINGS` set; prints the slowest steps and per-label totals.
* `tools/session_bench.py <repo>` — how much N concurrent sessions' gates serialize on each other: one
  clone per session, makespan and speedup per N, the USL fit (sigma = serialized fraction, kappa =
  interference), every lock wait by name, and the steps that inflated most. Pinned by `test_session_bench.py`.
* `tools/profiling/` — soak/profile harness. See its `README.md`.
* `install.sh` — wires `.githooks/` + starter `tools/gate.sh` / `.gatesrc`
  into a consumer repo. Pinned by `test_install.py`.
* `retired_hooks.sha256` — digest of every stock hook ever shipped; a repo hook matching one is pristine to `install.sh`
* `hooks/` — stock `pre-commit` (structural `--staged`) and `pre-push`
  (`tools/gate.sh --full`).
* `hooks/claude/skill_edit.sh` — a Claude Code PostToolUse hook (wired in `~/.claude/settings.json`,
  matcher `Write|Edit|MultiEdit`): an edit inside the skills corpus runs `goh skills` at once and
  a finding is exit 2 back to the writer. Not copied by `install.sh`. Pinned by `test_claude_skill_hook.py`.
* `tui/` — style source of truth. Pinned by `test_tui_integration.py`.
