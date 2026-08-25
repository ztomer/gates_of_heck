# CHANGELOG

## Unreleased

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
- `lib/golden_core.py` + `golden_diff.py`: shared pixel-diff core (mean abs
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
