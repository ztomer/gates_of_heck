# docs/map.md — script inventory

Every entry: what it does, when it runs, which test pins it.

## Gates (`gates/` — bash runners, sourced via `_common.sh`)

| Script | Purpose | Invoked | Test |
|---|---|---|---|
| `_common.sh` | Shared contract: fail-fast, print output, TUI, EXIT-trap sentinel. Sourced, never run. | every gate | `test_goh_init_trap.py` |
| `structural.sh` | Layer 1, every repo: emoji, conflict markers, file-length cap. `--staged` = pre-commit. (Disk hygiene was removed from this gate in v0.8.0 — see `check_disk_hygiene.py` below.) | `tools/gate.sh`, hooks | `test_gates_e2e.py`, `test_file_length_and_markers.py` |
| `py_gate.sh` | `ruff check` + `ruff format --check` + pytest with coverage floor. Args: `[repo] [pkg_dir]`. | `--full`, opt-in | `test_cwd_and_py_gate.py` |
| `rust_gate.sh` | `cargo fmt --check` + `clippy -D warnings` + optional `check_no_allow` + coverage floor when `GOH_COV_FLOOR_RUST`/`GOH_COV_FLOORS_JSON` is set (via `coverage_gate.sh --lang rust`, explicit argv). Args: `[repo] [cargo_dir]`. | `--full`, opt-in | `test_rust_gate.py` |
| `swift_gate.sh` | swiftlint (+ optional baseline ratchet) + cold build + test + coverage floor. `spm` or `xcode` mode. | `--full`, opt-in | `test_swift_gate_baseline.py`, `test_swift_gate_project_selection.py` |
| `swift_lint_baseline.py` | Helper: reconciles swiftlint JSON report against baseline (shrink-only). Called by `swift_gate.sh`, not directly. | via swift gate | `test_swift_gate_baseline.py` |
| `coverage_gate.sh` | ONE parameterized coverage gate: `--lang rust\|swift\|cpp\|py --floor N [--ignore RE] [--include RE] [--floors-json P] [--marker-ceiling P] [--engine E] [path]`. Floor: flag, then `GOH_COV_FLOOR_<LANG>`, else exit 2. | opt-in | `test_coverage_gate.py`, `test_coverage_strictest.py`, `test_coverage_rust_exports.py`, `test_coverage_gate_cpp_parse.py` |
| `coverage_swift.py` | Helper: swift coverage engine behind `coverage_gate.sh --lang swift` (llvm-cov export / xccov). | via coverage gate | `test_coverage_swift_selection.py` |
| `lcov_merge.py` | Helper: merges per-target lcov exports, strips CGU hashes. Called by coverage gate rust path. | via coverage gate | `test_lcov_merge.py` |
| `local_ci.sh` | Declarative step runner: steps from `GOH_CI_STEPS` + `--step`. Fail accumulator, logs on failure, optional per-step timeout (`GOH_LCI_TIMEOUT`). | opt-in | `test_local_ci.py` |
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
| `check_disk_hygiene.py` | Machine disk watch (scratch + cargo-cache ceilings). Standalone since v0.8.0 — invoked via `~/Projects/scripts/bin/disk_hygiene.sh`, NOT by any gate (pinned by `test_no_gate_runs_disk_hygiene`). | `test_check_disk_hygiene.py` |
| `check_shell_lint.sh` | `bash -n` + `shellcheck --severity=error` over tracked `*.sh` + `hooks/*`. Missing shellcheck degrades to syntax-only with a named warning. | `test_check_shell_lint.py` |
| `check_no_secrets.py` | Narrow secrets gate: known key prefixes + private-key headers, staged + full. No entropy heuristics by design. Revoked vectors suppress with `secret-ok: <reason>`. | `test_check_no_secrets.py` |
| `check_no_allow.py` | No `#[allow]` in Rust (repo-local twin; structural twin lives in consumer `tools/`). | `test_check_no_allow.py` |
| `check_no_screen_presentation.py` | Static half: test sources must not ask for screen APIs. | `test_screen_presentation.py` |
| `check_no_screen_linkage.sh` | Dynamic half: `nm -u` on built test binary must not import screen symbols. | `test_screen_linkage.py` |
| `check_swift_coverage.py` | Legacy helper for `swift_gate.sh` (SPM codecov JSON + xcresult walk). | `test_swift_coverage.py` |
| `check_baseline_ratchet.py` | Shrink-only ceilings (JSON or line baselines). | `test_check_baseline_ratchet.py` |
| `check_generated_fresh.py` | Artifact freshness: regenerate to sandbox, hash-compare. | `test_check_generated_fresh.py` |
| `check_tests_registered.py` | Every test source must be registered in a build block. | `test_test_registration.py` |
| `_gitutil.py` | Lib: repo root, NUL-delimited file lists, index bytes via `git show :path`. | `test_gitutil_paths.py` |

## Lib (`lib/`)

| Module | Purpose | Test |
|---|---|---|
| `golden_core.py` | Pixel-diff math only (no render, no bless, no `--update`). | `test_golden_core.py` |
| `mcp_scaffold.py` | stdio MCP JSON-RPC scaffold (framing, dispatch, serve). Re-exports `mcp_schema` names so old import paths keep working. | `test_mcp_scaffold.py` |
| `mcp_schema.py` | JSON-Schema validation split out of the scaffold (pure, no I/O). | `test_mcp_scaffold.py` |
| `eval_transport.py` | Grader/model-agnostic eval transport. | `test_eval_transport.py` |
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
* `hooks/` — stock `pre-commit` (structural `--staged`) and `pre-push`
  (`tools/gate.sh --full`).
* `tui/` — style source of truth. Pinned by `test_tui_integration.py`.
