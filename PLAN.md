# PLAN.md — fix plan from the 2026-08-24 review

One forward-looking backlog file (house rule #13: completed items get pruned
to git history, not left here to rot).

Findings reference the 2026-08-24 code review. Discipline: harness before bugs,
every new test proven red first, class fixes not instance fixes, one commit per
logical group, kill criterion per phase.

## Decisions made (2026-08-24)

| Question | Decision |
|---|---|
| Missing `checks/check_swift_coverage.py` | Implement it now (SPM + xcode), timeboxed |
| Cold-build default in swift_gate | `--full` forces `GOH_SWIFT_COLD=1`; xcode mode must honor it |
| Staged checks read worktree instead of index | Fix the class everywhere (emoji, length, conflict markers) |

## Phase 0 — Build the harness, prove it can fail

`tests/` is empty; nothing below is verified until this exists.

- [ ] `tests/conftest.py` + helpers: temp git fixture-repo builder (init, stage
      files, set index state) — needed because half the checks are git-coupled.
- [ ] `tests/test_check_no_emoji.py` — allow-list passes, ranges reject, VS16 /
      keycap rejected, `--staged` scope.
- [ ] `tests/test_check_file_length.py`, `test_conflict_markers.py` —
      pass/fail/binary-skip.
- [ ] `tests/test_check_no_allow.py` — `#[allow]` vs `#[expect]`,
      generated-marker exemption.
- [ ] `tests/test_wiring.py` — **the meta-gate**: parse every `gates/*.sh` and
      `hooks/*` for referenced script paths; assert each exists in the repo.
      Catches review findings #1 and #2 forever. Proven red against current HEAD.
- [ ] `tests/test_gates_e2e.py` — run `structural.sh --staged` inside a fixture
      repo; assert exit codes and output shape (`→ · ✓ ✗ ⚠` only).
- [ ] Run suite against HEAD → confirm wiring tests fail (red proof).

**Kill criterion:** fixture-repo helper > ~150 LOC → simplify to
subprocess-driven fixtures only.

## Phase 1 — The three broken wirings

1. [ ] **TUI dead check.** `tui/lib.sh` gains `_lib_info/_lib_ok/_lib_warn/`
       `_lib_err` aliases alongside the public names (`_common.sh:49` and
       `rust_gate.sh:43` gate on those names, so the styled lib never engages).
       Additive only; `_common.sh` unchanged at first. Test: sourcing
       `_common.sh` with the lib present routes through the styled path
       (stylerc sentinel value). Prove red by reverting the alias once.
2. [ ] **Missing Swift coverage checker.** Implement
       `checks/check_swift_coverage.py`: SPM mode parses `.build/**/codecov/*.json`;
       xcode mode parses the `.xcresult` via `xcrun xcresulttool`. Unit tests on
       checked-in sample payloads (no Xcode needed in tests).
3. [ ] **Hook path.** Write `install.sh` (README already promises it): installs
       `hooks/` via `core.hooksPath`, copies checks to `<repo>/tools/`, writes a
       starter `tools/gate.sh` + `.gatesrc`. Then **self-host**: install into
       gates_of_heck itself; a real pre-commit run passing is the acceptance test.

Three commits, one per fix. Each closes its Phase-0 red test.

## Phase 2 — Un-fork rust_gate.sh

- [ ] Rewrite as ~15 lines: source `_common.sh`, `goh_init "rust"`, three
      `goh_step`s (fmt, clippy, no-allow via `goh_optional_step
      tools/check_no_allow.py`). Removes the duplicated fallback block, the
      private mktemp log, and the `tail -40` vs `GOH_TAIL:-60` drift.
- [ ] Parity proof: same exit codes and output shape on a deliberately dirty
      fixture crate, old vs new script, before deleting the fork.

## Phase 3 — Stated-vs-implemented alignment

- [ ] `check_no_allow.py`: docstring wins over code — exemption window becomes
      "first 40 lines" as documented (code currently reads first 2000 chars);
      test pins both sides (marker at line 39 passes, line 41 fails).
- [ ] `swift_gate.sh`: `--full` exports `GOH_SWIFT_COLD=1` unless already set;
      xcode mode honors it by pinning `-derivedDataPath .build/xcode-dd` and
      wiping that path; header corrected to match reality.
- [ ] `hooks/pre-commit` failure message lists the FULL allow-set (`← ⌘ ⌥ ⌨`
      included) — sourced from one constant shared with the checker so message
      and policy cannot drift again.

## Phase 4 — Class fixes

Two named classes:

- **"Per-repo config baked into shared source"**
  - [ ] `check_no_emoji.py EXCLUDE_PREFIXES` → `--exclude` regex arg +
        `GOH_EXCLUDE_PREFIXES` from `.gatesrc`. Grep to confirm it was the only
        such constant.
- **"Pre-commit measured the wrong tree"** (staged mode reads worktree files,
  not index content — emoji, length AND conflict markers share this bug)
  - [ ] Shared `checks/_gitutil.py`: `staged_content(path)` reads
        `git show :path`; worktree fallback for non-staged runs. All three
        staged-mode checkers consume it.
  - [ ] Regression test: stage clean file A, put an emoji in A's worktree copy,
        assert checker still passes (and the inverse).

Also in this phase:

- [ ] `py_gate.sh` drops `env sh -c "... '$pkg_dir' ... $RUN ..."` string
      interpolation; steps built as argv arrays (or single `bash -c` with
      `printf %q` expansion). Breaks today on quotes/spaces in paths.
- [ ] `.gatesrc` and vendored `tui/lib.sh` resolved from
      `git rev-parse --show-toplevel` instead of CWD (`_common.sh:27`,
      `structural.sh:22`).

## Phase 5 — Verification & closeout

- [ ] Full own gate: `structural.sh --full` green on this repo; shellcheck
      clean; pytest green including red-proofs noted in test docstrings.
- [ ] User-POV pass: install hooks into one real consumer repo, make a
      violating commit, watch it blocked with correct output.
- [ ] README status table updated (`install.sh` and the coverage checker are no
      longer "not yet written"); CHANGELOG stanza; record the two classes above
      in the relevant skill notes per the metarule.

## Deliberately not shipping (with rationale)

- `cpp_gate.sh` / `kotlin_gate.sh` — no consumer repo needs them yet; graduate
  when one does (README layering rule).
- CI integration — README's stated contract is local-only by design.
- `check_disk_hygiene.py` truncated top-5 scan (first 400 children) — documented
  tradeoff, not a defect; revisiting costs minutes of du for marginal accuracy.
