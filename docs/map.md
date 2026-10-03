# docs/map.md — script inventory

Every entry: what it does, when it runs, which test pins it.

## Gates (`gates/` — bash runners, sourced via `_common.sh`)

| Script | Purpose | Invoked | Test |
|---|---|---|---|
| `_common.sh` | Shared contract: fail-fast, print output, TUI, EXIT-trap sentinel. Sourced, never run. | every gate | `test_goh_init_trap.py` |
| `_goh_bin.sh` | The ONE resolution of the native `goh` binary (`GOH_NO_NATIVE`, then `GOH_BIN` trusted or reported, then `bin/goh`, then `PATH`). Sourced, never run. | `structural.sh`, `goh.sh` | `test_goh_entry.py` |
| `goh.sh` | `goh.sh <check> [args]`: the one way a consumer runs a house checker — native when a binary resolves, else the Python checker it ports, same arguments, fallback stated once. Consumers call this, never `checks/*.py` or `bin/goh` directly. | `rust_gate.sh`, consumer repos | `test_goh_entry.py` |
| `structural.sh` | Layer 1, every repo: emoji, conflict markers, file-length cap, shell lint, secrets, markdown-link resolution, Cargo.lock/manifest agreement, version provenance. Execs the native `bin/goh` when built (`scripts/build-goh.sh`), else the Python checkers. `--staged` = pre-commit. (Disk hygiene was removed from this gate in v0.8.0 and from this repo in v0.8.1 — it lives in `~/Projects/scripts`.) | `tools/gate.sh`, hooks | `test_gates_e2e.py`, `test_file_length_and_markers.py` |
| `py_gate.sh` | `ruff check` + `ruff format --check` + pytest with coverage floor. Args: `[repo] [pkg_dir]`. | `--full`, opt-in | `test_cwd_and_py_gate.py` |
| `py_staged.sh` | `ruff check` + `ruff format --check` on the STAGED `*.py` only; the cheap half of `py_gate.sh` at commit time. Args: `[repo]`. | every commit, opt-in | `test_py_staged.py` |
| `rust_gate.sh` | `cargo fmt --check` + `clippy -D warnings` + `check_no_allow` + `check_no_empty_assert` + coverage floor when `GOH_COV_FLOOR_RUST`/`GOH_COV_FLOORS_JSON` is set (via `coverage_gate.sh --lang rust`, explicit argv). Args: `[repo] [cargo_dir]`. | `--full`, opt-in | `test_rust_gate.py` |
| `swift_gate.sh` | swiftlint (+ optional baseline ratchet) + cold build + test + coverage floor. `spm` or `xcode` mode. | `--full`, opt-in | `test_swift_gate_baseline.py`, `test_swift_gate_project_selection.py` |
| `swift_lint_baseline.py` | Helper: reconciles swiftlint JSON report against baseline (shrink-only). Called by `swift_gate.sh`, not directly. | via swift gate | `test_swift_gate_baseline.py` |
| `coverage_gate.sh` | ONE parameterized coverage gate: `--lang rust\|swift\|cpp\|py --floor N [--ignore RE] [--include RE] [--floors-json P] [--marker-ceiling P] [--engine E] [path]`. Floor: flag, then `GOH_COV_FLOOR_<LANG>`, else exit 2. | opt-in | `test_coverage_gate.py`, `test_coverage_strictest.py`, `test_coverage_rust_exports.py`, `test_coverage_gate_cpp_parse.py` |
| `coverage_swift.py` | Helper: swift coverage engine behind `coverage_gate.sh --lang swift` (llvm-cov export / xccov). | via coverage gate | `test_coverage_swift_selection.py` |
| `lcov_merge.py` | Helper: merges per-target lcov exports, strips CGU hashes. Called by coverage gate rust path. | via coverage gate | `test_lcov_merge.py` |
| `local_ci.sh` | Declarative step runner: steps from `GOH_CI_STEPS` + `--step`. Fail accumulator, logs on failure, optional per-step timeout (`GOH_LCI_TIMEOUT`). Every step goes through the proven-step cache. | opt-in | `test_local_ci.py`, `test_proven.py` |
| `proven.sh` | `proven.sh [--label L] [--log F] -- '<step>'`: run one step unless that exact step string already passed on this exact clean tree (same gates, toolchains, keyed env) within `GOH_PROVEN_TTL_S`. For a repo hook whose step also appears in `GOH_CI_STEPS`, so the push-time run is a hit. | repo hooks | `test_proven.py` |
| `_proven.sh` | The proven-step cache itself (key, lookup, record, prune), sourced by `proven.sh` and `local_ci.sh` so the key a hook records and the key local CI looks up cannot drift. Sourced, never run. | via the two above | `test_proven.py` |
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

## Checks (`checks/` — called by gates)

| Script | Purpose | Test |
|---|---|---|
| `check_no_emoji.py` | Emoji policy gate (allow-list in `ALLOWED_ORDERED`). `--staged` polices the index. | `test_check_no_emoji.py` |
| `check_no_conflict_markers.py` | Fails on merge markers. | `test_file_length_and_markers.py` |
| `check_file_length.py` | `--max N` file-length cap. | `test_file_length_and_markers.py` |
| `check_shell_lint.sh` | `bash -n` + `shellcheck --severity=error` over tracked `*.sh` + `hooks/*`. Missing shellcheck degrades to syntax-only with a named warning. | `test_check_shell_lint.py` |
| `check_no_secrets.py` | Narrow secrets gate: known key prefixes + private-key headers + a credential-named key (`api_key`, `password`, …) with a 32+ char quoted value, staged + full. No entropy heuristics by design. Revoked vectors suppress with `secret-ok: <reason>`. | `test_check_no_secrets.py` |
| `check_no_allow.py` | No `#[allow]` in Rust (repo-local twin; structural twin lives in consumer `tools/`). | `test_check_no_allow.py` |
| `check_no_empty_assert.py` | No `assert!(x.is_empty())` / `x.len() == 0` in `assert!`, where clippy's own suggestions (`assert_eq!(x.len(), 0)`, `assert_ne!`) are left alone. Exists because clippy reports NOTHING for an assert carrying a message — measured, and it left 5 instances here two commits after a sweep for the same lint. `--probe` runs the measured table. | `test_check_no_empty_assert.py` |
| `check_no_screen_presentation.py` | Static half: test sources must not ask for screen APIs. | `test_screen_presentation.py` |
| `check_display_seam.py` | The OTHER screen gate, and not a copy of the one above: that one refuses screen APIs in TEST TARGETS, this one refuses them in APP SOURCE unless they route through a seam the consumer's JSON policy names. Input is the same problem as presentation (a real pointer read, an event tap, a cursor warp all compete with the user for their own mouse), so both share `swift_code_only`. A live-tier harness must CALL the policy's helper with a substantive, non-placeholder reason, and the executor may sit on an earlier line than the command — the shape `ruff format` emits. Every tracked `*.sh` is scanned whatever `scan` says. A policy that will not parse, names a key this checker does not know, names a seam that does not exist, or matches zero source is exit 2, never a pass. `--probe` proves every rule red-first. Came from `games/ZeroThunder`'s repo-local gate (SUPERSOTA R5/R6). | `test_display_seam.py` |
| `_marker_reason.py` | Lib: an escape-hatch marker must carry a real justification — a bare `// screen-ok:` silences nothing. Moved here from `games/ZeroThunder/tools/marker_reason.py`, where eight of that repo's gates imported it, rather than copied for the seam gate. Gates INTENT, not EFFECT: a fabricated reason is the same KIND of sentence as a true one. | `test_display_seam.py`, `test_marker_reason.py` |
| `_display_seam_probe.py` | Lib: the self-proof for `check_display_seam.py`, split out so neither file crowds the 500-line cap. One case per pattern in the Swift and live-command vocabularies, plus the config refusals. | `test_display_seam.py` |
| `check_no_screen_linkage.sh` | Dynamic half: `nm -u` on built test binary must not import screen symbols. | `test_screen_linkage.py` |
| `check_swift_coverage.py` | Legacy helper for `swift_gate.sh` (SPM codecov JSON + xcresult walk). | `test_swift_coverage.py` |
| `check_swift_warnings.py` | One `swift build --build-tests`; any warning in the repo's own dirs fails, colours and OSC 8 hyperlinks stripped first (for repos that judge build output instead of `-warnings-as-errors`). | `test_check_swift_warnings.py` |
| `check_baseline_ratchet.py` | Shrink-only ceilings (JSON or line baselines). | `test_check_baseline_ratchet.py` |
| `check_generated_fresh.py` | Artifact freshness: regenerate to sandbox, hash-compare. | `test_check_generated_fresh.py` |
| `check_lock_version.py` | A committed `Cargo.lock` must agree with the manifests it was generated from: every workspace member's lockfile version against its own manifest (with `version.workspace = true` RESOLVED, never compared as the literal `true`), plus the declared release version where a source OUTSIDE the manifests declares one. app_updates, 2026-10-01: a release commit bumped `[workspace.package] version` to 1.36.0 and shipped 1.35.0 in the lockfile — invisible because `cargo build` silently rewrites it, so the working tree heals while the commit does not. Not gated on being a Rust repo; absence is a named non-run. `--probe` proves it red. | `test_check_lock_version.py` |
| `check_md_links.py` | Every relative markdown link resolves to an existing file AND an existing anchor: GitHub heading slugs with duplicate counters, explicit `{#id}`, `<a id=…>`. app_updates, 2026-10-01: an audit had to hand-derive `#48-what-90-can-actually-do-measured` into a sibling file and had already written a wrong one; a broken anchor renders fine and 404s on click. http(s)/mailto/site-absolute are named skips and COUNTED; code spans, fenced and indented blocks are not scanned. `--exclude RE` for vendored docs. `--probe` proves it red. | `test_check_md_links.py` |
| `check_tag_version.py` | A pushed `refs/tags/v<semver>` must name the version its OWN COMMIT declares (never the working tree). Runs first in `push_gate.sh`, over the refs git hands the hook, so a retag of a commit the remote already has is still judged. `GOH_TAG_VERSION_SOURCES` picks the layouts; a glob matching nothing is reported, and a tag with no version source at all is a finding. `--probe` proves it red. | `test_check_tag_version.py`, `test_check_tag_version_push_gate.py` |
| `check_version_provenance.py` | A `--version` flag carrying no commit. STATIC by design (a gate that must run every repo's binary is a gate that gets skipped on exactly the repos it would catch). Scans `src/main.rs`, `src/bin/*.rs` and `src/lib.rs` of every package that builds a binary; an OPAQUE `*_PROVENANCE` clause is accepted only when the setting build script derives a commit, a date AND the working tree's state; a baseline entry naming a file that declares no version is a FINDING. `--probe` proves it red. | `test_check_version_provenance.py` |
| `check_tests_registered.py` | Every test source must be registered in a build block. | `test_test_registration.py` |
| `_gitutil.py` | Lib: repo root, NUL-delimited file lists, index bytes via `git show :path`, `foreign_repo_env()` for git on any repo but the gated one. | `test_gitutil_paths.py`, `test_hook_git_env.py` |
| `_cargo_toml.py` | Lib: Cargo manifest/lockfile text -> values. Pure, hand-rolled (a hook resolves whatever `python3` is on PATH, often 3.9 with no `tomllib`). Split out because "what does a TOML table say" is a question three files answer and a third answer is how they drift. | `test_check_lock_version.py` |
| `_md_text.py` | Lib: markdown text -> the links it contains and the anchors it generates. GitHub slugs with duplicate counters, explicit ids, setext headings; code is skipped for LINKS and not for ANCHORS (a code span inside a heading contributes its inner text). Pure, because a hand-rolled anchor derivation is the defect the checker exists to end. | `test_check_md_links.py` |
| `_rust_crates.py` | Lib: a Cargo tree -> every package dir, every file that could be a binary, and what a package's build scripts set. Split out of `check_version_provenance.py` as the half that knows about the FILESYSTEM, leaving the checker holding only its rules. `main_files` is passed IN, not owned: the set of files that may declare a version is the checker's scope, and the checker's own calibration narrows `MAIN_FILES` in the checker's source -- a scope constant living here would have moved that line out of reach of every test that patches it. | `test_check_version_provenance.py` |
| `check_estate_corpus.py` | SUPERSOTA R3: every declared house checker, handed a REAL subtree copied out of a real consumer repo with one violation planted inside it, must still go red and name the planted file. Six entries, each with the corpus and the reason it was chosen — the `check_no_empty_assert` entry replays the 2026-10-02 receiver bug in the exact shape it failed in (a string literal INSIDE the receiver). A corpus below a file floor, a corpus that is not CLEAN under the checker before the plant lands (so a red cannot be credited to the plant), and a plant that would land in a stub are all refusals. Copies, never runs in place: these are other people's working trees. `--probe` proves a checker that cannot fail is caught. | `test_estate_corpus.py` |
| `_calibration.py` | Lib: reads `checks/gate_calibration.json` and holds every claim to the estate -- the key names a gate that exists, a cited prover resolves, is COMMITTED, and either ran a self-proof green in this sweep or (outside this repo) lists the key among what it proves. Called by `check_probes_pass.py`, which is what runs in every repo, so the registry is read on every gate run and a consumer repo with no registry is reported rather than failed. | `test_gate_calibration.py` |
| `_calibration_probe.py` | Lib: the self-proof for `_calibration.py`, split out so neither file crowds the 500-line cap. Runs from `check_probes_pass.py --probe`; asserts every way the registry can lie goes red and the sound registry comes back clean. | `test_gate_calibration.py` |

## Lib (`lib/`)

| Module | Purpose | Test |
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
  on `--full`: `-n 8 --dist loadgroup`, serial fallback without xdist).
* `tools/release-kit/` — `release.sh` (gate → stanza → tag → push → release),
  `gen_app_icons.py`, `update_dev.sh`. Pinned by `test_release_kit.py` (+
  `test_release_hardening.py`, `test_profiling_scripts.py` for profiling).
* `tools/profiling/` — soak/profile harness. See its `README.md`.
* `install.sh` — wires `.githooks/` + starter `tools/gate.sh` / `.gatesrc`
  into a consumer repo. Pinned by `test_install.py`.
* `retired_hooks.sha256` — digest of every stock hook ever shipped; a repo hook matching one is pristine to `install.sh`
* `hooks/` — stock `pre-commit` (structural `--staged`) and `pre-push`
  (`tools/gate.sh --full`).
* `tui/` — style source of truth. Pinned by `test_tui_integration.py`.
