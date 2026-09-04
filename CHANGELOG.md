# CHANGELOG

## v0.8.0 — disk watch out of CI, pooled du, parallel suite _(2026-09-04)_

Measured first: full `structural.sh` was 17.7s, of which `check_disk_hygiene`
was 15.1s (a du stat-storm: $TMPDIR 7.4s + 37GB shared cargo-target 4.1s +
project targets 1.5s). The checkers themselves total 0.45s — a Rust rewrite
would speed up the wrong 3%.

**What shipped**:
- Disk hygiene OUT of the gate: `structural.sh` no longer runs it on any
  scope (full tree now ~0.5s, was ~17.7s). Pinned by
  `test_no_gate_runs_disk_hygiene` — re-adding a gate reference fails red.
- The watch lives on as `~/Projects/scripts/bin/disk_hygiene.sh` (thin
  wrapper, same delegation via `GOH_DIR`), unified with
  `reclaim_build_space.sh`: a failure names the reclaimer as the fix.
  Covered by `scripts/tests/test_disk_hygiene.sh` (both directions).
- `check_disk_hygiene.py`: one ThreadPoolExecutor over ALL roots (two pools
  still stacked the slowest scratch root on the slowest cache dir — 12.4s;
  one pool: 15.1s → ~8s) + `GOH_SCRATCH_ROOTS` seam for scoping/tests.
- Parallel suite: `tools/gate.sh --full` uses `pytest -n 8 --dist loadgroup`
  when xdist is installed (loadgroup keeps xdist-grouped files on one
  worker; ungrouped files spread by load), serial + warn otherwise.
- Trim: the 36-line fake-gh stub duplicated in both release suites now lives
  once in `tests/conftest.py` (`test_release_kit.py` 497 → 456 lines);
  pruned a duplicate `import pytest`; fixed stale `ci_local.sh` name and
  `loadfile` references across docs.

**What deliberately didn't ship**:
- No Rust rewrite (evidence says it buys nothing here).
- No launchd schedule for the watch (manual/periodic by hand for now).

**Tests**: 3 new (wiring removal-pin + config-schema seam coverage via
existing tests + scripts wrapper suite); 442 pass in ~40s parallel
(`-n 8 --dist loadgroup`), was 441 in ~168s serial. Baseline gate: full
`structural.sh` 17.7s → 0.5s; standalone watch ~8s (was 15.1s in-gate).

## v0.7.0 — LLM-navigability pass _(2026-09-04)_

Everything from the review of what slows an LLM down here, fixed
additively — no gate behavior changed, no key renamed.

**What shipped**:
- `AGENTS.md`: agent entry point (commands, layout, rules, config pointer).
- `docs/map.md`: every gate/checker/lib/tool with purpose + pinning test;
  coverage-file overlap (`coverage_gate.sh` vs `coverage_swift.py` vs
  `check_swift_coverage.py` vs `lcov_merge.py`) resolved in prose.
- `docs/config.md` + `.gatesrc.example`: single schema for all ~30 `GOH_*`
  keys (README documented ~10); defaults, readers, and the
  `GOH_EXCLUDE` ∪ `GOH_LINE_EXCLUDE` union rule.
- `tests/test_config_schema.py`: drift gate — a new `GOH_*` key without a
  `docs/config.md` row goes red (red-proven with a synthetic key).
- `docs/contracts.md`: nine load-bearing invariants, each naming its test.
- `docs/new-checker.md`: test-first contributor cookbook (fixture helpers,
  `chr(0x...)` rule, staged/index testing, wiring + schema obligations).
- Cutover: `README.md` points at the new docs; `install.sh` starter
  `.gatesrc` points at `docs/config.md` / `.gatesrc.example`.

**What deliberately didn't ship**:
- No `GOH_*` renames, no coverage-file merges, no hook behavior changes.

**Tests**: 2 new in `tests/test_config_schema.py`; 441 total pass.
Baseline before the pass: 439 pass.

## v0.6.0 — coverage strictest convergence, disk cargo-cache watch, MCP Tax parity _(2026-08-26)_

Convergence to the strictest coverage standard across toolchains, third-class
disk leak monitoring, and MCP Tax parity. 399 → 439 tests.

Coverage convergence to strictest standard:
- `coverage_gate.sh`: positive inclusion filtering before ignore (`--include` /
  `GOH_COV_INCLUDE_RE`), per-target and per-file floors with tolerance/exempt
  rules (`--floors-json` / `GOH_COV_FLOORS_JSON`), and shrink-only forgiven-lines
  ceiling (`--marker-ceiling` / `GOH_COV_MARKER_CEILING`). Backward-compatible
  with single `--floor`.
- `coverage_swift.py` & `lcov_merge.py`: include filtering for llvm-cov export /
  parse_xccov, multi-shape floor config loading, per-file floor enforcement with
  stale-exempt detection, and shrink-only marker ceilings.

Disk hygiene, local CI & release kit:
- `check_disk_hygiene.py`: watches shared `CARGO_TARGET_DIR` (`~/.cache/cargo-target`)
  and per-project targets for unbounded cache growth (`--max-cache-dir-gb` /
  `GOH_MAX_CACHE_GB`, default 50 GB) with largest-child diagnostics.
- `local_ci.sh`: preserves failing step log directories on exit so
  reproducibility seeds printed at top of output are retained.
- `release.sh`: unsets `GOH_RELEASE_BUFFERED` after startup snapshot so nested
  invocations and gate test runs remain hermetic and self-buffering.

MCP scaffold & eval transport:
- `lib/mcp_scaffold.py`: Tax SDK parity with server instructions support, input
  schema validation before tool handlers (`-32602 INVALID_PARAMS`), protocol
  version negotiation (`HANDSHAKE_VERSIONS` / `LATEST`), and `tool_from_function`
  helper. Server-death crash class closed and JSON-RPC conformance pinned.
- `lib/eval_transport.py`: Ollama environment variable aliasing (`OLLAMA_API_BASE` /
  `OLLAMA_HOST`, `OLLAMA_MODEL`) as fallbacks behind EVAL / OPENAI keys.

## v0.5.0 — second adversarial pass _(2026-08-25)_

Re-review of v0.4.0 by three fresh hunters (fixes-as-hostile-code, oracle
fuzzing, untouched surfaces) plus refute-first verification. 399 tests.

Fail-open closed:
- `goh_init`'s EXIT trap erased crash exit codes — a gate with a syntax error
  exited 0 and a real consumer commit shipped silently green. Exit 0 is now
  honored only after the completion sentinel; any other path re-raises.
- `coverage_gate` laundering hole: a failing export that left its output file
  behind passed completeness ("100%" over garbage). Success now requires
  per-target `.ok` markers; the redundant count check is gone.
- `py_gate`'s bare `--cov` floored only modules the tests happened to import —
  a never-imported module at 0% passed `GOH_PY_COV_MIN=100`. Coverage is now
  scoped to the package.

Fuzz-proven hardening (oracle oracles, thousands of seeded cases):
- PNG fallback decoder: all corrupt-input escapes are named preconditions
  (were raw tracebacks); **zero wrong-pixel decodes across 12k corruptions** —
  the load-bearing property, now pinned.
- lcov merger extracted to `gates/lcov_merge.py`: BOM'd part files no longer
  silently drop their first record (a real 75% showed as green 100%); FN
  format detected by content; malformed records exit 2 naming file:line.
- Baseline ratchet rejects huge-int JSON (OverflowError traceback) and
  liberal numeric literals in line baselines.
- `local_ci` steps run from the repo root; stdin isolation regression-pinned.

Also: screen-check line lists derive from `\n` (control characters crashed
the probe); `check_no_allow` depth-counter scan fixes an FP and a found FN;
coverage_swift pairs profdata with mtime-nearest binary and refuses ambiguity;
wiring meta-gate parses guarded lines (typo'd refs can't ship blind);
NO_COLOR honors the spec; styled TUI output routes warnings to stderr with
data-safe printf; multiple xcodeproj candidates die instead of locale-picking;
emoji ranges extended (geometric shapes, astral forward-compat) with zero
blast radius across consumers. Consumer forks (divoom ×2, monitor,
koffee_big) got the round-one `-z` fix too.

## v0.4.0 — adversarial review: every finding fixed _(2026-08-25)_

Three independent hunters + refute-first verifiers over checks, shell gates and
consumer seams; 19 confirmed findings, all fixed with regression tests built
from their repros. 289 → 355 tests.

Gate-bypasses closed:
- `_gitutil` now lists paths NUL-delimited — quoted non-ASCII filenames were
  silently skipped by EVERY checker in staged AND full mode.
- `local_ci` steps no longer inherit the loop's stdin — a stdin-reading step
  swallowed all remaining steps and reported "all passed" exit 0.
- `coverage_gate`: a per-target lcov export that fails is a hard failure naming
  the target (rust + cpp) instead of a warn over partial measurement.
- `check_swift_coverage` SPM mode refuses payloads that parse to zero
  measurable files (was: "100% OK", exit 0).
- golden tolerances and ratchet values reject NaN/Infinity (NaN defeated both
  in every direction); non-finite is a named precondition, never a verdict.
- `check_no_emoji` adds the singleton emoji codepoints outside scanned ranges;
  ©®™ documented as permitted typography — bare forms pass, VS16 presentation
  still fails.

False positives removed:
- `check_no_screen_presentation`: single left-to-right state scanner — `//`
  inside a string no longer blanks real code later on the line, and `/*` in a
  string can't open a fake block span.
- `check_no_allow` ignores comment mentions of `#[allow]` (the prose a cleanup
  PR writes), still catches real attributes.
- `coverage_gate` merger parses both lcov FN formats; three-field spans bound
  by parsed end (two-field legacy behavior pinned byte-identically — moving it
  would shift consumer coverage numbers).

Half-states / hardening:
- `release.sh`: stale-tag at an older commit hard-fails naming both SHAs;
  awk stanza matching no longer eats regex backslashes via `-v`; artifact
  fetch failures name the step.
- `install.sh` records installed-hook hashes — re-running bootstrap no longer
  clobbers hand-extended hooks; `--force` overrides.
- `mcp_scaffold` returns isError for unserializable tool results instead of
  dying mid-session; subprocess timeouts kill whole process groups
  (`lib/killtree.py`); disk hygiene measures through partial du failures and
  checks free space per scratch root; local_ci runs steps from the repo root;
  profiling scripts carry line-1 shebangs.
- Self-host: GOH's own `--full` now runs its test suite — a gate bug can no
  longer reach eleven consumers without failing here first. README documents
  `git commit --no-verify` as the escape hatch.

## v0.3.0 — post-unification hardening _(2026-08-25)_

- `checks/check_no_screen_presentation.py`: bare-identifier matches in Swift
  TYPE position (annotations, params, returns, casts, generics) no longer
  flag; use positions (`NSScreen.main`, `.screens`) still do. Erases the
  fake-protocol-surface markers ZoneTilerWM reported.
- `tools/release-kit/release.sh` hardening: `--no-push` implies skipping the
  GitHub-release step; the script self-buffers at startup so a concurrent
  edit can no longer corrupt a running invocation (field-reported as a silent
  exit 0 — worse than a crash); stanza extraction keeps `###` subsections and
  tags carry them via `--cleanup=verbatim`; X.Y versions and the
  missing-CHANGELOG guard are pinned by regression tests.
- `gates/swift_gate.sh` + `gates/swift_lint_baseline.py`: `GOH_SWIFT_LINT_BASELINE`
  turns the lint stage into a shrink-only ratchet — baselined violations tolerated,
  NEW ones fail named, vanished ones are a re-record nudge. Match key (file, rule,
  reason) probed against swiftlint 0.65.1: code motion and severity flips stay
  tolerated, reason drift and other-file twins do not. Unset keeps bare strict
  linting. Red-proven both directions.

## v0.2.0 — the harness unification _(2026-08-25)_

Maximalist centralization: sixteen per-repo harness families moved under one
roof. 74 → 262 tests, every new checker red-proven.

- `lib/desktop_lock/`: canonical machine-wide desktop mutex relocated from
  `~/Projects/scripts/lib` (PID+start-time record contract preserved verbatim).
- `checks/check_baseline_ratchet.py`: shrink-only ceilings (JSON or line
  baselines) replacing monitor/necrohand/ZeroThunder ad-hoc ratchets.
- `checks/check_generated_fresh.py`: artifact-freshness gate (regenerate to a
  temp sandbox, hash-compare, timeouts mandatory) — the Taxes pattern,
  generalized.
- `checks/check_no_screen_presentation.py` + `lib/headless_env.sh` +
  `checks/check_no_screen_linkage.sh`: the two-halves screen invariant —
  static grep (absorbed necrohand's full pattern set, differential 3/13 →
  14/14), runtime env contract, and an nm -u link-table audit.
- `checks/check_tests_registered.py`: every test source on disk must be
  registered inside an add_executable/add_test block (stronger than the
  CadGoose/CadGoose2 verbatim twins it replaces).
- `lib/golden_core.py`: shared pixel-diff core (mean abs
  diff, changed fraction, SSIM; Pillow-or-pure-Python); blessing stays repo
  policy by design. Offscreen-render cookbook recorded in `docs/harnesses.md`.
- `lib/eval_transport.py` + `lib/mcp_scaffold.py`: grader/model-agnostic eval
  transport (parse-rate guardrail, atomically resumable sweeps) and stdio
  MCP JSON-RPC scaffold matching the newline-delimited framing all four
  ancestor servers speak.
- `tools/profiling/`: the CadGoose soak/profile harness canonized (the two
  repos carried byte-identical copies); target-root seam so neither repo
  profiles the wrong checkout.
- Migrations shipped across eleven consumer repos (app_updates, monitor,
  divoom-control, CadGoose, CadGoose2, sys_updater, routines, necrohand,
  koffee_big, ZeroThunder, ZoneTilerWM): forked length checks deleted,
  coverage/local-CI delegating with step lists diffed old-vs-new, locks and
  ratchets on the shared implementations. Honest keeps documented where a
  local contract was strictly stronger.
- Class fixes found BY the unification: cargo target enumeration via
  find(1) silently lost directory-style test suites (now cargo metadata);
  cpp coverage drove display-taking tests on the user's desktop (now honors
  GOH_CTEST_ARGS); release-kit stanza extraction truncated at ### headings;
  update_dev quit-detection no-op and signature clobbering (PROCESS_NAME,
  verify-then-adhoc).
- `tools/release-kit/`: ONE parameterized releaser (`release.sh` — gate →
  changelog stanza → idempotent annotated tag → push → gh release → Homebrew
  tap bump; every step skippable, every failure names its step, `--dry-run`
  prints without executing), one Apple icon pipeline (`gen_app_icons.py` —
  sips normalize → fixed ten-member ladder → iconutil .icns + optional modern/
  legacy appiconset), and a templated dev installer (`update_dev.sh` — the
  union of the koffee/necrohand/routines copies: quit-before-replace, ditto,
  absolute-path xattr with post-clear verification). Covered by
  `tests/test_release_kit.py` against real git + local bare remotes and a
  stateful fake gh.
- `gates/coverage_gate.sh`: ONE parameterized coverage gate
  (`--lang rust|swift|cpp|py --floor N [--ignore RE] [path]`) replacing six
  per-repo copies that drifted. Rust mode ports the app_updates implementation
  (per-test-target lcov exports + CGU-hash normalization, exact uncovered-line
  reporting); swift/cpp/py adapt necrohand+ZeroThunder, CadGoose2 and
  sys_updater respectively. Floors resolve `--floor` → `GOH_COV_FLOOR_<LANG>`
  → named exit-2 failure; exclusion regexes pass through verbatim, never
  invented. Real cargo llvm-cov e2e test (skipped when the toolchain is absent).
- `gates/local_ci.sh`: ONE declarative step runner for the ~10 copy-pasted
  local-CI orchestrators — steps from `.gatesrc` `GOH_CI_STEPS` (colon-
  separated) and/or `--step` args, tui-styled steps, logs captured to a temp
  dir and dumped on failure, fail accumulator (a failing step never stops the
  run), `--dry-run`, nonzero exit iff any step failed.
- Post-unification adoptions: necrohand/koffee_big/ZeroThunder MCP servers
  ported onto `lib/mcp_scaffold.py` under characterization-pinned wire parity
  (Taxes keeps the official SDK — it provides validation and surface the
  scaffold does not); ZeroThunder's golden suite delegates its pixel math to
  `lib/golden_core.py` (bit-exact parity proven over the baseline corpus);
  ZoneTilerWM's ui sweep likewise (exactly equal changed-pixel counts on all
  70 surfaces) and enabled the swift gate over its lint baseline, fixing a
  pre-existing actor-isolation error for real.

## v0.1.1 _(2026-08-25)_

- License: MIT.
- Policy: permit functional Mac key glyphs (`← ⌘ ⌥ ⌨ ⇧ ⌃ ⏎ ⎋ ↵`) and the text
  operators `⇒ ⇄`; per-repo extras via `GOH_ALLOW` in `.gatesrc`.
- Hygiene: untrack `.opencode` session state; ignore it.

## Gate wiring, harness, and class fixes _(2026-08-24)_

First full audit of the factored-out gate suite found three broken wirings,
two self-violations of the repo's own thesis, and several stated-vs-implemented
drifts. Everything was fixed test-first: the Phase-0 harness was written
against the broken tree and its 10 red failures each mapped to one finding.

**What shipped**:

- `tests/` harness: fixture-repo builder, per-checker contract tests, the
  wiring meta-gate (`tests/test_wiring.py` — parses every gate/hook for
  referenced scripts and asserts they exist), e2e `structural.sh --staged`
  runs, and real-`git commit` hook tests through `core.hooksPath`.
- `checks/check_swift_coverage.py` — shipped; both swift_gate modes called it
  and it never existed. SPM codecov JSON + xcresulttree walk; missing payloads
  are exit 2 with a named reason, never a fake 0%.
- `install.sh` + delegation hooks — hooks resolve the shared checkout via
  `GOH_DIR` (default `~/Projects/gates_of_heck`) so there is exactly one copy
  of every checker; pre-commit runs structural `--staged`; pre-push names the
  missing `tools/gate.sh` instead of exec-failing. This repo is self-hosted.
- `tui/lib.sh` publishes `_lib_*` names via private `_tui_*` impls — the
  styled lib could never engage before (the `_lib_info` probe was always
  false). Naive alias-to-public-name first attempt recursed to a bash
  segfault; caught by the new harness before commit.
- `gates/rust_gate.sh` un-forked onto `_common.sh` (~20 lines); parity proven
  against the preserved legacy fork (`tests/fixtures/rust_gate_legacy.sh`) on
  real crates across six scenarios. Added `goh_step_in`: in-dir steps as pure
  argv, no shell-string interpolation.
- Class fixes: `checks/_gitutil.py` makes ALL staged checks measure the index
  (`git show :path`), not the worktree; `EXCLUDE_PREFIXES` replaced by
  `--exclude` regex from `GOH_EXCLUDE`; `.gatesrc` and vendored tui resolved
  from the git root (subdirectory-safe).
- Stated-vs-implemented: `@generated` window is now the documented "first 40
  lines"; `GOH_SWIFT_COLD` defaults to 1 as claimed and xcode mode honors it
  by wiping the pinned `.build/xcode-dd`; emoji failure message generated from
  the `ALLOWED_ORDERED` constant (was hand-copied, omitting ← ⌘ ⌥ ⌨).

**What deliberately didn't ship**: `cpp_gate.sh` / `kotlin_gate.sh` (no
consumer yet — graduate on third use); CI integration (local-only by design);
disk-check top-5 truncation (documented tradeoff).

**Caught by the gates during this work**: a literal U+21D2 in a test comment
blocked by the freshly installed pre-commit hook (dogfood working as intended);
OS python3 3.9 crashing on `bytes | None` annotations at def time, found by
the self-host test.

**Tests**: 73 new across `tests/`; suite is 70 tests + 6 rust parity tests,
all green; red-proof receipts recorded in test docstrings.
