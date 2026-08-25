# CHANGELOG

## Unreleased

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
