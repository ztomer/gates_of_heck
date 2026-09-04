# docs/config.md — every `GOH_*` key

Single schema. CLI flags beat env/.gatesrc where both exist. Unset means
"gate default or skip" per row — never a silent pass on a missing floor
(coverage gates exit 2 when no floor is stated anywhere).

## Structural (`gates/structural.sh` + `checks/`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_MAX_LINES` | unset (check skipped + warn) | File-length cap. Set `500` in every repo. |
| `GOH_EXCLUDE` | unset | Regex on repo-relative paths, exempt from BOTH emoji scan and length cap (vendored/generated trees). |
| `GOH_LINE_EXCLUDE` | unset | ADDITIVE to the length check only. Effective length exemption = `GOH_EXCLUDE` ∪ `GOH_LINE_EXCLUDE`. Paths named only here are still emoji-scanned. |
| `GOH_ALLOW` | unset | Extra permitted characters (regex chars, spaces stripped). Keep empty unless the repo genuinely needs it. |
| `GOH_MAX_SCRATCH_GB` | `25` | Scratch-dir ceiling, full runs only (`checks/check_disk_hygiene.py`). |
| `GOH_MAX_CACHE_GB` | `50` | Cargo-cache ceiling (`~/.cache/cargo-target` + per-project targets). |
| `GOH_MIN_FREE_GB` | `20` | Free-space floor per scratch root. |
| `GOH_WATCH_PATHS` | `~/.cache/cargo-target` + `~/Projects/*/target` | Colon-separated extra cache dirs to watch. `--watch-paths` flag overrides it. |

## Python (`gates/py_gate.sh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_PY_COV_MIN` | unset (runs bare `pytest`, warns) | Coverage floor %. Scoped to the package (`--cov=<pkg_dir>`), so never-imported modules count as 0%. |
| `GOH_PY_RUNNER` | `.venv/bin/python -m` if executable, else `python3 -m` | Toolchain prefix, whitespace-split (`uv run` works; a runner path containing a space cannot be expressed — use `.venv/bin/python`). |

## Rust (`gates/rust_gate.sh`)

No `GOH_*` knobs. `sccache` comes from `RUSTC_WRAPPER`, not from here.

## Swift (`gates/swift_gate.sh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_SWIFT_MODE` | `spm` if `Package.swift` exists, else `xcode` | `spm` or `xcode`. Unknown value is exit 1. |
| `GOH_SWIFT_SCHEME` | unset (required in xcode mode) | Xcode scheme for `xcodebuild test`. |
| `GOH_SWIFT_PROJECT` | auto (single `*.xcodeproj`) | Pick the project. Multiple candidates without this key is a named refusal (never locale-picks). |
| `GOH_SWIFT_COV_MIN` | unset (no coverage step) | Coverage floor %. |
| `GOH_SWIFT_COLD` | `1` | `1` wipes ALL of `.build` before build+test (cold on purpose). `0` allows incremental. |
| `GOH_SWIFT_LINT_BASELINE` | unset (bare `swiftlint --strict`) | Path to baseline JSON. When set, lint becomes a shrink-only ratchet: baselined violations tolerated, new ones fail named, vanished ones print a re-record nudge. |

## Coverage (`gates/coverage_gate.sh` + `gates/coverage_swift.py`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_COV_FLOOR_RUST` / `_SWIFT` / `_CPP` / `_PY` | unset | Per-language floor. Resolution: `--floor` flag, then this key, else exit 2 naming both seams. |
| `GOH_COV_FLOORS_JSON` | unset | JSON file with per-target + per-file floors (`--floors-json`). |
| `GOH_COV_INCLUDE_RE` | unset | Positive filter: only matching files are measured, applied BEFORE `--ignore` (`--include`). |
| `GOH_COV_MARKER_CEILING` | `.coverage-forgiveness-ceiling.json` if present | JSON cap on `cov:ignore` forgiven lines, shrink-only (`--marker-ceiling`). |
| `GOH_COV_SWIFT_ENGINE` | `spm` | Swift engine: `spm` or `xcodebuild` (`--engine`). Meaningless outside `--lang swift` — rejected there. |
| `GOH_COV_SCHEME` | unset (required for xcodebuild engine) | Scheme for the xcodebuild coverage run. |
| `GOH_COV_DD` | `.build/xcode-dd` | Derived-data dir holding the profdata. |
| `GOH_COV_XCRESULT` | unset | Skip the run: totals from an existing xcresult via xccov. No line-level data there, so `cov:ignore` markers alongside it are exit 2. |
| `GOH_CPP_BUILD_DIR` | `build-cov` | CMake coverage build dir. |
| `GOH_CPP_TEST_BIN` | auto (single test executable) | Pick the binary. Zero/multiple candidates without it is exit 2. |
| `GOH_CTEST_ARGS` | unset | Extra ctest selection, verbatim. |

## Local CI (`gates/local_ci.sh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_CI_STEPS` | unset (required unless `--step` given) | Colon-separated shell-command list. `.gatesrc` steps run first, then `--step` ones. Keep colons OUT of step strings — put `${VAR:+flag}` logic in a repo script and invoke that. |

## Release kit (`tools/release-kit/release.sh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_RELEASE_GATE` | unset (required) | Gate command a release must pass (`--gate CMD` flag beats it). |
| `GOH_RELEASE_BUFFERED` | internal | Self-buffering sentinel set at startup; unset after snapshot so nested invocations stay hermetic. Do not set by hand. |

## Profiling (`tools/profiling/`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_PROFILE_TARGET` | repo root | Absolute repo root the profilers operate on. Env override for profiling a different checkout. |

## Runtime / harness

| Key | Default | Meaning |
|---|---|---|
| `GOH_DIR` | `~/Projects/gates_of_heck` | Location of this shared checkout. Hooks and gates resolve checkers through it; that is how one fix reaches every repo. |
| `GOH_HEADLESS` | unset (policy off) | `1` (or truthy) enforces offscreen policy; `"" 0 false no off` mean off. Forward with `env $(headless_env)`. Unset with `env -u GOH_HEADLESS` for deliberate live runs. See `docs/harnesses.md`. |
| `GOH_HEADLESS_REFUSAL_EXIT` | `3` | Exit code of `headless_require_live` refusals (distinct from 1 = ran and failed). |
| `GOH_TAIL` | `60` (gates) / `30` (local_ci) | Lines of captured log printed on step failure. |
| `GOH_AWK_VER_RE` | internal | Version regex passed into the release stanza matcher. Not user config. |

Internal-only (not `.gatesrc` policy): `GOH_ROOT`, `GOH_GIT_ROOT`,
`GOH_REPO_ROOT`, `GOH_NAME`, `GOH_LOG`, `GOH_LOGS`, `GOH_COMPLETED`, `GOH_EX`.
