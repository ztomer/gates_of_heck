# docs/config.md — every `GOH_*` key

Single schema. CLI flags beat env/.gatesrc where both exist. Unset means
"gate default or skip" per row — never a silent pass on a missing floor
(coverage gates exit 2 when no floor is stated anywhere).

## Structural (`gates/structural.sh` + `checks/`, native: `bin/goh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_BIN` | unset | Explicit path to the native `goh` binary. Set-but-missing is reported once and the Python checkers run; unset resolves `bin/goh` then `goh` on `PATH`. |
| `GOH_NO_NATIVE` | unset | Set to `1` to force the Python checkers even when a binary is available (the parity tests use it to drive the Python side). |
| `GOH_SKIP_BUILD` | unset | For `install.sh`: install hooks without building `bin/goh`. |
| `GOH_MAX_LINES` | unset (check skipped + warn) | File-length cap. Set `500` in every repo. `off` records a deliberate no-cap (an info line instead of the warning) for a repo whose files are long by design; say why in a comment beside it. |
| `GOH_EXPORT_KEEP` | unset | Ignored files the pre-push export must carry (space-separated, repo-relative), e.g. a compile-flag marker the manifest reads. The gate runs on a clean worktree of the pushed commit (`gates/push_gate.sh`); tracked files are always there, ignored ones only if named here. |
| `GOH_PUSH_WORKTREES` | `~/.cache/goh/push` | Where `push_gate.sh` checks the pushed commit out. Never the temp directory: SwiftLint's baseline matches nothing under `/private/tmp` or `$TMPDIR` and everything under `/Users` (measured 2026-09-21). |
| `GOH_PUSH_LOGS` | `~/.cache/goh/push-logs` | Where `push_gate.sh` tees each run's output. A green run deletes its log; a red one keeps it and prints the path (newest 20 kept), so a refused push never loses its evidence. |
| `GOH_TAG_VERSION_SOURCES` | `file:VERSION cargo:Cargo.toml` | Version sources `push_gate.sh` checks a pushed `refs/tags/v<semver>` against — space-separated `kind:path`. Kinds: `file` (first meaningful line), `cargo` (`[workspace.package]`/`[package]` `version`), `swift` (any string constant whose value is `x.y.z` — a SwiftPM package that declares its version in source, where the constant is usually named `marketing` or `version` and is usually accompanied by a build number that is NOT a release number; `swift:` returns EVERY release-shaped constant, so a file declaring two different ones still fails rather than one being picked). `plist` (`<key>`/`<string>` pairs in an Apple bundle `Info.plist`; without it a bundle's version is unreadable, because `file:` reads the XML **declaration** `<?xml version="1.0"…?>` as the version and reports a correct release as a mismatch — measured on ZeroThunder's `v2.10.0`, which no other kind could check) `pyproject` (the `[project] version` a Python distribution declares; without it a packaging-only repo has no readable source and `file:` reads whatever the first meaningful line happens to be) A `path` may be a glob, expanded against the TREE AT THE COMMIT; scope it (`cargo:crates/*/Cargo.toml`) rather than `**`, which sweeps `vendor/` and invents findings about crates this repo does not ship. Empty or all-unknown is exit 2, never a pass over nothing. |
| `GOH_STEP_TIMEOUT` | **1800** | The wall-clock CEILING on every `goh_step` in every toolchain gate (`structural.sh`, `rust_gate.sh`, `py_gate.sh`, `swift_gate.sh`) and on every step the native `bin/goh` delegates. Printed on each step line; `0` opts out and says `UNBOUNDED` rather than looking identical to a bounded step; non-numeric is exit 2, because a typo'd ceiling must not silently leave the step unbounded. `goh_step` had **no** ceiling while `local_ci.sh` did, so one hang had two answers; the native tier had none either and now routes every delegated step through `lib/bounded_run.py`, so all three paths share one implementation of the bound. |
| `GOH_STEP_GRACE` | 5 | Seconds between the process-group SIGTERM and SIGKILL when a ceiling expires. |
| `GOH_EXCLUDE` | unset | Regex on repo-relative paths, exempt from BOTH emoji scan and length cap (vendored/generated trees), and from `check_no_unreaped_spawn.py` — so a vendored Rust crate's own test modules are not policed as if they were ours. **Anchor it.** The match is `re.search`, so `'vendor/'` is a SUBSTRING test: it exempts `rust/src/myvendor/a_tests.rs` exactly as it exempts `vendor/camoufox-rs`, silently. Write `'^vendor/'`. Measured 2026-10-04 on `ztools`, which declares the unanchored form: with it, 106 first-party test files stay in scope and a violation planted in one is still red — so it is narrow in effect today — while the same violation at a path merely *containing* `vendor/` is exempt with no output at all. |
| `GOH_LINE_EXCLUDE` | unset | ADDITIVE to the length check only. Effective length exemption = `GOH_EXCLUDE` ∪ `GOH_LINE_EXCLUDE`. Paths named only here are still emoji-scanned. |
| `GOH_LINE_BASELINE` | unset (check skipped + warn when `GOH_LINE_EXCLUDE` is set) | Path to the repo's shrink-only ratchet baseline. Enables two checks: every `GOH_LINE_EXCLUDE` entry over the cap carries a ceiling there — an exemption from the cap is not an exemption from every bound — and the ratchet itself: each listed file is measured (`wc -l`) and may not exceed its ceiling. Lower a number when a split lands; never raise one to absorb growth. Format: `<lines><tab><path>` per line (`#` comments) or a JSON object mapping path to lines. |
| `GOH_LINE_UNBOUNDED` | unset | Regex naming the `GOH_LINE_EXCLUDE` entries that legitimately need no ceiling — captured or vendored material that must stay emoji- and secret-scanned but is not ours to split. State the reason beside it in `.gatesrc`. A pattern matching no tracked file FAILS. |
| `GOH_ALLOW` | unset | Extra permitted characters (regex chars, spaces stripped). Keep empty unless the repo genuinely needs it. |
| `GOH_SKILLS_CORPUS` | unset (check skipped) | Set to `1` in a repo that IS an agent skills corpus (`~/.claude/skills`, `~/.agents/skills`) to run `check_skills_corpus.py`. Opt-in rather than auto-detected, so a repo that merely SHIPS example skills is unaffected. |
| `GOH_CROSS_REPO_ROOT` | unset (not needed) | For `gates/push_gate.sh`, and set BY it: the real checkout of the repo being pushed. The export worktree holds the pushed commit's TRACKED files, so a repo whose evidence is its sibling repositories — a metarepo, whose children are separate repos rather than submodules — has none of that evidence in the export, and every cross-repo citation resolves to nothing. Measured 2026-10-03 on `games`: 9 findings on a commit whose checker exits 0 in the working tree. A checker may read this ONLY for citations that name a child; it is deliberately not a symlink of the children into the worktree, which would put sibling sources inside the parent's scanned tree and fail a parent push on a child's finding. The worktree's isolation is untouched: it guards a repo's OWN files, and those still come from the worktree. |
| `GOH_SKILLS_ROOT` | the repo root | Corpus root, when the skills tree is a subdirectory rather than the repo itself. |
| `GOH_NO_HOME_PATHS` | unset (check skipped) | Set to `1` to run `check_no_home_paths.py`: no `/Users/<x>/…`, `/home/<x>/…`, `~/Projects/…` or `$HOME/Projects/…` in any tracked text file (a `${VAR:-default}` expansion is derived, not hard-coded, and passes). A path that must stand carries `path-ok: <reason>` on the line or above. Opt-in per repo — turn it on once the tree is clean, never red in twenty places at once. Same `GOH_EXCLUDE`. |
| `GOH_PYTHON_FORMATTED` | unset (check skipped) | Set to `1` to run `check_python_formatted.py`: every tracked `.py` must match what `ruff format` produces, using the rule set the repo declares in `pyproject.toml` / `ruff.toml`. Opt-in per repo, both scopes: `--staged` judges the INDEX blobs of the staged `.py` files (`ruff format --check --force-exclude --stdin-filename`) and reformats nothing, so a commit is refused for its own bytes only; full scope judges the tree. (It was full-only until v0.21.0, and two unformatted files passed pre-commit and refused the v0.20.0 push.) The checker reads ruff's settings from the tree it runs in, so with no configuration it answers from ruff's defaults and from the directory it was launched from — which is why the flag exists rather than a default-on step. It refuses to skip when `ruff` is absent: an uninstalled formatter is a missing gate, not a pass. |
| `GOH_CLAIM_DERIVATION` | unset (check skipped) | Set to `1` to run `check_claim_derivation.py`: a **MARKED** number in prose is re-derived from the tree. Three adversarial reviews of the games estate converged on one class (2026-10) — `roadmap_state.py` claimed "64 declared gates" against 70 in `verify.py`, `check_mcp_server.py` claimed "477 lines and 18 characterization tests" for 321 and 20, `gaf/README.md` claimed 86 files / 1059 tests for 101 / 1732, and each was found by hand at hours apiece. **The claim form is explicit, and the looser rule was rejected**: a claim is read only when the number AND the thing it counts are both in backticks (this estate's existing convention that a backtick means "this is the evidence"), or when the line carries a leading `claim:` marker. A bare number beside a path is prose, because reading bare numbers means reading English — a gate cannot tell an inventory from an incident, a current claim from a quoted one, or a count from a version, and it is wrong LOUDLY when it is wrong. **Four units, each derived exactly**: `lines` (`awk 'END{print NR}'` — not `wc -l`, which reads a file with no final newline one line short), `tests` (test functions by `ast`, a parametrised case counting once), `gates`/`steps`/`checks` (elements of a NAMED module-level list, by `ast` — never a line regex, which is what read 64 against 70), and `files` (the length of `git ls-files`'s own answer for `DIR/*.EXT`). Every finding prints the command that re-derives the truth, and `--probe` EXECUTES those commands against fixtures, so a finding a reader cannot check cannot ship. Survivors go in `claim_derivation_allow.json` at the repo root: one claim in one file each, with a `reason` and a `status` (`legitimate` or `unreviewed`); **a stale entry fails**, and matching collapses whitespace so `ruff format` cannot revoke an exemption. Opt-in per repo, seeded first: this lands RED in every repo in the estate, because every repo in the estate has this defect, and a gate that goes red in twenty places on the day it lands is a gate that gets disabled. Fenced blocks are examples in every text file, and in Python a non-docstring string literal is data rather than prose — that is where a checker's own fixture lives, and a gate that cannot express its test cases cannot be tested. Same `GOH_EXCLUDE`. |
| `GOH_NO_KILL_BY_NAME` | unset (check skipped) | Set to `1` to run `check_no_kill_by_name.py`: no `pkill` or `killall` by name, and no `pgrep`/`pidof` feeding a `kill`, in tracked code and scripts (a `pkill -P`/`-g`/`-s` is owner-scoped and passes). A name matches processes the caller does not own. Survivors go in `kill_by_name_allow.json` at the repo root: one exact line in one file each, with a `reason` and a `status` (`legitimate` or `unreviewed`); a stale entry fails. Opt-in per repo, seeded first. Same `GOH_EXCLUDE`. |
| `GOH_SKILLS_MAX_WORDS` | `5000` | Word ceiling on each `SKILL.md` — the file that loads on invoke. Oversized skills are ratcheted in `skills_size_baseline.json` beside the corpus: shrink-only, and a stale entry FAILS. |

## Disk watch (standalone: `~/Projects/scripts/bin/disk_hygiene.sh`)

NOT a gate since v0.8.0 — a du stat-storm over host trees does not belong
in a commit gate. These keys configure the standalone watch (env or the
checker's flags, forwarded verbatim by the wrapper). When it fails, the
fix is `reclaim_build_space.sh` next to it.

| Key | Default | Meaning |
|---|---|---|
| `GOH_MAX_SCRATCH_GB` | `25` | Scratch-dir ceiling. |
| `GOH_MAX_CACHE_GB` | `50` | Cargo-cache ceiling (`~/.cache/cargo-target` + per-project targets). |
| `GOH_MIN_FREE_GB` | `20` | Free-space floor per scratch root. |
| `GOH_WATCH_PATHS` | `~/.cache/cargo-target` + `~/Projects/*/target` | Colon/comma/space-separated cache dirs to watch. `--watch-paths` flag overrides it. |
| `GOH_SCRATCH_ROOTS` | `$TMPDIR` + `/tmp` (auto) | Override scratch roots (same separators). Scope production runs; point tests at fixtures. |

## Python (`gates/py_gate.sh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_PY_COV_MIN` | unset (runs bare `pytest`, warns) | Coverage floor %. Scoped to the package (`--cov=<pkg_dir>`), so never-imported modules count as 0%. |
| `GOH_PY_RUNNER` | `.venv/bin/python -m` if executable, else `python3 -m` | Toolchain prefix, whitespace-split (`uv run` works; a runner path containing a space cannot be expressed — use `.venv/bin/python`). |

## Rust (`gates/rust_gate.sh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_RUST_LINT_CONFIGS` | unset | Extra clippy configurations to lint, `:`-separated, each a string of cargo argv (e.g. `--target x86_64-unknown-linux-musl -p agent`). The gate's own clippy step covers ONE cfg -- this machine's target with all features on -- and a crate that is part `cfg(target_os = ...)` or part `cfg(feature = ...)` has halves that command never compiles and therefore cannot report on. Each entry runs `cargo clippy --all-targets <argv> -- -D warnings`. A `--target` whose std is not installed is a hard failure naming the `rustup target add`, never a skip: a step that inspects nothing must not read as a pass. Unset leaves the single-cfg behaviour with a printed nudge. |
| `GOH_RUST_LINT_CARGO` | `cargo` | The cargo command `GOH_RUST_LINT_CONFIGS` entries that name `--target` run through; host entries keep `cargo`. `cargo-zigbuild` brings `zig cc`, which a target build script's C needs (ring for musl, from macOS); it and `zig` are then required up front (`required_tools.py` layer `rust-cross`). Any other value must be on PATH. |

`sccache` comes from `RUSTC_WRAPPER`, not from here.

### Dependency currency (`checks/check_dep_currency.py`, run as `goh.sh deps`)

A step in `rust_gate.sh`. Two severities on purpose, because conflating them is
how a currency check becomes a gate nobody reads.

| Key | Default | Meaning |
|---|---|---|
| `GOH_DEPS_STRICT` | unset | `1` also fails on a MAJOR behind. Off by default: a major moves in its own commit by house rule, and a patch is routine, so failing on drift would be red on every honest commit. |
| `GOH_DEPS_RATCHET` | unset | Path to a file of crate names this repo has already TRIAGED as behind. Any major-behind NOT listed fails, so the set can shrink but not grow back. |
| `GOH_DEPS_OFFLINE` | unset | `1` skips the crates.io arm and PRINTS that it did. Absence of evidence is not a clean bill, and a check that prints "clean" because the index was unreachable converts a missing measurement into evidence. |
| `GOH_CRATES_IO_CACHE` | `~/.cache/goh/crates-io` | Where crates.io answers are kept, one file per crate name, shared by every crate and repo; `off` asks every time. Read only by the report-only arm; an UNREACHED lookup is never cached. |
| `GOH_CRATES_IO_TTL_S` | `21600` (6 h) | How old a cached answer may be. Under `GOH_DEPS_STRICT` / `GOH_DEPS_RATCHET`, a release newer than this can be missed for up to the TTL -- the stated price of not asking crates.io once per dependency per crate per push. |

FATAL with no key set at all, and needing no network: a direct dependency
pinned BELOW a version the graph already resolves. Our pin is then why two
majors of one crate are in the tree — and that makes the same type not the same
type, which the compiler reports by NAME and never by pointing at the pin.

## Swift (`gates/swift_gate.sh`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_SWIFT_MODE` | `spm` if `Package.swift` exists, else `xcode` | `spm` or `xcode`. Unknown value is exit 1. |
| `GOH_SWIFT_SCHEME` | unset (required in xcode mode) | Xcode scheme for `xcodebuild test`. |
| `GOH_SWIFT_PROJECT` | auto (single `*.xcodeproj`) | Pick the project. Multiple candidates without this key is a named refusal (never locale-picks). |
| `GOH_SWIFT_COV_MIN` | unset (no coverage step) | Coverage floor %. |
| `GOH_SWIFT_WARN_DIRS` | top-level dirs of git-tracked `*.swift` | Xcode mode: comma-separated first-party dirs whose compiler warnings fail the gate (`checks/check_swift_warnings.py` over the `xcodebuild test` log). SPM mode builds with `-warnings-as-errors` instead. |
| `GOH_SWIFT_COV_FLOORS` | unset | Path to a per-target floors JSON (same schema as `coverage_gate.sh --floors-json`), checked BEFORE the package floor. Keys are path prefixes relative to `Sources/`, matched segment-wise, so `App/Core` floors a directory without splitting the package into targets. A key matching no measured source is exit 2, never a silent pass. Pointing at a missing file is a named refusal. |
| `GOH_SWIFT_COLD` | `1` | `1` wipes ALL of `.build` before build+test (cold on purpose). `0` allows incremental. |
| `GOH_SWIFT_LINT_BASELINE` | unset (bare `swiftlint --strict`) | Path to baseline JSON. When set, lint becomes a shrink-only ratchet: baselined violations tolerated, new ones fail named, vanished ones print a re-record nudge. |

## Coverage (`gates/coverage_gate.sh` + `gates/coverage_swift.py`)

| Key | Default | Meaning |
|---|---|---|
| `GOH_COV_FLOOR_RUST` / `_SWIFT` / `_CPP` / `_PY` | unset | Per-language floor. Resolution: `--floor` flag, then this key, else exit 2 naming both seams. `rust_gate.sh` passes its floor as explicit argv when set (`.gatesrc` values are not exported); `coverage_gate.sh` also honors them from the environment on direct invocation. |
| `GOH_RUST_COVERAGE` | unset | Environment, set by a repo's `tools/gate.sh` for its COMMIT gate: `defer` makes `rust_gate.sh` skip the coverage step by name ("coverage deferred to the push gate") because the push gate checks the floor; any other value fails. A release touches every crate, and one cold instrumented build per crate per commit was most of a 13-minute release. |
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
| `GOH_CI_JOBS` | **1** (serial) | How many `GOH_CI_STEPS` run at once. Reports still come out in DECLARED order and the fail accumulator is unchanged. Steps prefixed with resource tags, `[db,screen] cmd`, never overlap a step sharing a tag (tags: letters, digits, `_.=/-`; no colon, which separates steps -- write `cargo=crates/x`). Serial by default because only the repo knows which of its steps write the same file. Non-numeric or `0` is an exit-2 config error. |
| `GOH_RUST_JOBS` | **4** | Crates gated at once by `rust_gate.sh --each-crate` (through `local_ci.sh`'s scheduler). Non-numeric or `0` is a config error. |
| `GOH_RUST_GROUPS` | env, internal | Which of `rust_gate.sh`'s groups (`crate`, `repo`, `coverage`, comma-separated) a run proves; set by `--each-crate` (`repo` once, `crate,coverage` per crate). Read from the ENVIRONMENT before `.gatesrc`, so a repo file cannot narrow its own gate. An unknown word fails. |
| `GOH_LCI_TIMEOUT` | **900** | Per-step wall-clock ceiling in seconds. Expired steps are TERM-then-KILLed **in their own process group** — the whole subtree, not just direct children — and fail named with exit 124 plus the pids that were still running. Non-numeric values are exit-2 usage errors. `0` opts out and must be typed. Was `unset` = no limit, which meant a hung `cargo test` produced no observable at all: the run that would have reported it was the run that had been killed (media_server, 2026-10-03). |
| `GOH_STEP_TIMEOUT` | **1800** | The same ceiling for every `goh_step` in the toolchain gates (`rust_gate.sh`, `py_gate.sh`, `swift_gate.sh`, `structural.sh`). `goh_step` had **no** ceiling at all, so the same hang was bounded in one path and unbounded in the other. The ceiling is printed on every step line; `0` opts out and says `UNBOUNDED` rather than looking identical to a bounded step. Non-numeric is exit 2 — a typo'd ceiling must not silently leave the step unbounded. |
| `GOH_STEP_GRACE` | 5 | Seconds between the group SIGTERM and the group SIGKILL when a ceiling expires. |

Every gate step also runs under `lib/orphan_canary.py`: the process table is sampled either side of
each step and anything that outlived it — attributable to this repo by path, or orphaned (`ppid 1`)
— is **reported and the step fails**. A leaked server is invisible from the inside: the step exits 0
and the orphan blocks every later run. Processes started elsewhere on the machine are counted and
named as such, never failed on.

## Proven steps (`gates/proven.sh` + `gates/_proven.sh`, used by `gates/local_ci.sh`)

A step that exited 0 on a clean tree (working tree == index) is recorded under
`<git-common-dir>/goh-proven/`; the same step string on the same tree, with the
same gates checkout, toolchains and keyed environment, is skipped within the
TTL. Rationale and the full key: the header of `gates/proven.sh`.

| Key | Default | Meaning |
|---|---|---|
| `GOH_PROVEN` | unset (on) | `0` disables the cache: every step runs, nothing is recorded. |
| `GOH_PROVEN_TTL_S` | `86400` | Record lifetime in seconds; older records are ignored and pruned. Non-numeric values are exit-2 usage errors. |
| `GOH_PROVEN_ENV` | unset | Space-separated environment variable NAMES (in `.gatesrc`) whose values join the key, beside the built-in `CI RUSTFLAGS RUSTDOCFLAGS CARGO_BUILD_TARGET PYTHONPATH`. Name a variable here when a step's verdict depends on it. |
| `GOH_PROVEN_NOW` | internal | Clock seam for the tests (epoch seconds). Not user config. |

`rust_gate.sh` proves its three groups separately (`gates/_rust_proven.sh`): the crate group and coverage on the crate's own inputs (`goh rust-scope`), the repo-wide scans on the whole tree. The same `GOH_PROVEN` / `GOH_PROVEN_TTL_S` apply; every `GOH_*` in the environment is part of each group's key.

## Per-file verdict cache (BACKLOG P4)

A ported checker whose verdict is a pure function of one file and its tools records it per file
(`crates/goh/src/verdict_cache.rs`), keyed on EVERY input: today `goh shell-lint`, keyed on both
tools' identities (path, size, mtime), the flags, `SHELLCHECK_OPTS`, every `.shellcheckrc`
shellcheck would read, the display path and the file's sha256. An unreadable or malformed record
is a miss; a run whose shellcheck output cannot be attributed to one file records nothing.
Measured on media_server: `structural --full` 1.9 s -> 0.30 s warm.

| Key | Default | Meaning |
|---|---|---|
| `GOH_VERDICT_CACHE` | unset (on) | `0` disables it: every file is judged by the tools, nothing is recorded. |
| `GOH_VERDICT_DIR` | `~/.cache/goh/verdicts` | Where the records live, one directory per checker. |

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
| `GOH_FAIL_PATTERN` | `error:\|FAILED\|failed\|panicked at\|Assertion\|✗` | On step failure, the grep whose hits are printed BEFORE the tail — the failing cases a long suite buried above it. |
| `GOH_FAIL_LINES` | `40` | How many of those hits are printed. |
| `GOH_TIME` | unset | When set (any value), `goh_step` appends per-step elapsed whole seconds to its ok line. Off by default. |
| `GOH_TIMINGS` | unset | A file path. Every bounded step of both structural tiers, every `goh_step`, and every `local_ci.sh` step (and its proven-cache hits, marked `"cache": "hit"`) appends ONE JSON line: `label`, `ms`, `rc`, `tier`, `parent`, `cwd` (`lib/step_timings.py`). Whole lines under `O_APPEND`, so concurrent gates share one file. A failed write is dropped: the instrument never changes a verdict. Steps run with `GOH_STEP_TIMEOUT=0` (unbounded) are not timed. Report: `python3 lib/step_timings.py report FILE`; front end: `tools/gate_profile.sh <repo>`. |
| `GOH_TIMINGS_PARENT` | set by `lib/bounded_run.py` | The label of the timed step a child runs inside, so a nested gate's lines name their parent. Not a setting: written for children, read by the recorder. |
| `GOH_TIMINGS_CWD` | set by `goh_step_in` | The directory a timed step really runs in (`goh_step_in` changes directory inside the child), so the timing line of each crate's step names that crate. Not a setting. |
| `GOH_PROFILE_PROVEN` | unset | `tools/gate_profile.sh` only: keep the proven-step cache on while profiling (default off, because a profile of cache hits measures the cache, not the work). |
| `GOH_AWK_VER_RE` | internal | Version regex passed into the release stanza matcher. Not user config. |
| `GOH_TREE_STAMP_FILE` | internal | The working tree's stamp taken at the first `goh_init` (`lib/tree_stamp.py`); compared in `goh_done` and on a red exit. Not user config. |
| `GOH_TREE_CHECKED` | internal | Set once the stamp has been compared, so the EXIT trap does not report the move twice. Not user config. |
| `GOH_RELEASE_VERSION` | set by `tools/release-kit/release.sh` | The version being cut, exported into the `--verify CMD` run (and the archive build) so the command can assert the INSTALLED binary answers with exactly it. Read it, never set it by hand. |
| `GOH_SWIFT` | unset (`xcrun --find swift`, then PATH) | Explicit swift driver for `swift_gate.sh` and any script sourcing `gates/swift_toolchain.sh`. Refused when not executable. Exists because a swiftly toolchain on PATH shadowed Xcode's and the gate failed for three days as "environmental". |

Internal-only (not `.gatesrc` policy): `GOH_ROOT`, `GOH_GIT_ROOT`,
`GOH_REPO_ROOT`, `GOH_NAME`, `GOH_LOG`, `GOH_LOGS`, `GOH_COMPLETED`, `GOH_EX`, `GOH_NATIVE_BIN` (the resolved native binary inside `structural.sh`), `GOH_TMPDIRS` (temp dirs the EXIT trap removes), `GOH_INDEX_VIEW` (the index export `goh_index_view` hands a whole-tree checker at `--staged`).

## Which gate source runs (C4)

Consumers never run the shared checkout's working tree: every entry point re-runs itself from an
immutable export of its HEAD (`gates/_from_head.sh`).

| Key | Default | Meaning |
|---|---|---|
| `GOH_LIVE` | unset (consumers run HEAD) | `1` runs the gates from the shared checkout's WORKING TREE. Unset, every entry point (`structural.sh`, `rust_gate.sh`, `push_gate.sh`, ...) and sourced lib (`_common.sh`, `tui/lib.sh`) re-runs itself from an immutable export of the checkout's HEAD (`gates/_from_head.sh`, C4), so an uncommitted edit to gate source cannot judge any consumer's commit. Set it to develop the gates; this repo's suite sets it (`tests/conftest.py`), and a gate started under pytest without it refuses. The native binary follows: see `GOH_LIVE_TARGET_DIR`. |
| `GOH_HEAD_CACHE` | `~/.cache/goh/head` | Where the HEAD exports live, one immutable directory per commit; exports of other commits untouched for 7 days are pruned. |
| `GOH_LIVE_TARGET_DIR` | `<checkout>/target/goh-live` | Under `GOH_LIVE`, when the working tree's Rust (crates/, Cargo.toml, Cargo.lock, rust-toolchain.toml) differs from HEAD, `_goh_bin.sh` builds THAT `goh` here, once per diff, and runs it -- `bin/goh` is HEAD's (C3) and would answer for code the run is not testing. |
| `GOH_LIVE_ROOT` | internal | Set by `_from_head.sh` for the export's children: the live checkout, where `bin/goh` (not in git) is found. Not user config. |
