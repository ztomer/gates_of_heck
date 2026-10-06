# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-06, v0.23.0 (read first)

v0.23.0 retired the Python checkers: `bin/goh` is the only structural tier. Box: 16 cores, 4-5 busy
at idle, sys ~= user, so **spawn count, tree walks and network round trips are the cost metric, not
CPU**; `-n 12` is slower than `-n 8`. Every wall-clock number below needs a QUIET box (load < 4):
the 2026-10-06 session ran at load 12-31 beside other sessions' gates, so its timings are not
measurements. Landed plans are pruned to this table; their detail is in the CHANGELOG and the
commit bodies.

| landed | commit | measured |
|---|---|---|
| N1 every delegated checker native (unreaped-spawn, provenance, kill-by-name, claims, md-links, lock-version, credential-urls, python-formatted, shell-lint, deps, empty-assert, tag-version) | `4707bc4`..`079fda3` | media_server `structural --full` 4.9 -> 1.9 s; unreaped-spawn 2.87 -> 0.13 s |
| P4 per-blob verdict cache for shell lint | `3e4f2e9` | media_server `--full` 1.9 -> 0.30 s warm (target 1.5 s: MET) |
| N3 stage A: the binary is the only tier; `.gatesrc` the only pipeline config | `96018bd` | -- |
| N2 + N3 stage B: ported Python deleted, the spec frozen at `96018bd` | `a6f8f33` | suite 2090 tests ~116-140 s -> 1806 ~90 s (both under load) |
| R5 a new vendored copy of a house checker refused at commit | `b4046e3` | -- |
| P5 (work) no hidden cargo builds, no twice-run tiers | `916a37a` | summed test time 693 -> ~620 s under load |
| C2 (gate half) an external corpus judged at its commit | `46cf078` | -- |
| C2 (writer half) `hooks/claude/skill_edit.sh`, a PostToolUse hook: the corpus judged when a skill is written | -- | -- |
| P0-P4, C1, C3-C5 | v0.20.0-v0.22.0 | see CHANGELOG |

## Open — measurements (each needs a quiet box)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands.

| what | baseline | last | target |
|---|---|---|---|
| this repo's suite, `-n 8 --dist loadgroup` | 94.4 s (v0.20) | 85-93 s from load 4.6 (2026-10-06; the suite itself drives load to 18), summed 600 s | <= 60 s |
| media_server push, one crate changed, warm | 197 s | not re-measured since P1a/b/d, P3 | <= 30 s |
| media_server push, everything changed, warm | 177 s (P0) | not re-measured | <= 90 s |
| any consumer's pre-commit structural layer | ~1 s | not re-measured since N1 | <= 0.4 s |
| P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines | 186/147/138 s (1/2/4, busy) | provisional | the knee, recorded |

It misses: 600 s summed over 8 workers is 75 s before any packing loss, so 60 s needs ~150 s of
summed time cut, not better packing. The levers by summed time (2026-10-06, `--durations=0`):
`test_rust_gate_scoped_cache.py` 47 s of real cargo builds, `test_display_seam.py` 44 s (a whole
1.2 s probe per neutered rule x 24; sys time, i.e. one `git ls-files` per fixture, dominates),
`test_rust_gate.py` 30 s, `test_proven.py` 23 s, `test_claim_derivation_native_parity.py` 21 s,
`test_swift_gate_baseline.py` 16 s (13 s is the one real-swiftlint test, `-m "not slow"` skips it).

## Downstream: what each consumer session needs to know

- **Every repo:** re-run `$GOH_DIR/install.sh <repo>`; `structural.sh` names a hook that is an older
  stock. **A Rust toolchain is now required** for layer 1 (`bin/goh` is the only tier;
  `required_tools.tsv` names `cargo`, and `build-goh.sh` refuses up front without it).
- **monitor:** its CI runs `GOH_NO_NATIVE=1 bash .gates_of_heck/gates/structural.sh --full`. The key
  is retired and ignored (said once); drop it. Its customised pre-commit (`check_gate_parity.py`) and
  working-tree pre-push generation are monitor's call; its pre-commit runs the full test suite,
  which P3 lets the push reuse.
- **antiknob:** `tools/_gitutil.py` is a vendored copy of a house file; `goh structural --full` now
  names it (a NEW copy is refused at commit). Delete it and import the shared one via `PYTHONPATH`.
- **Consumers calling a Python checker by path** (`check_no_emoji.py`, `check_file_length.py`,
  `check_python_formatted.py`, `check_version_provenance.py`, `check_tag_version.py`,
  `check_no_allow.py`): they still work, as forwarders to `goh.sh <check>`; move to
  `bash "$GOH_DIR/gates/goh.sh" <check>`. Any other retired path is gone; `goh --help` lists checks.
- **servers:** adopt `rust_gate.sh --each-crate` and `GOH_RUST_LINT_CARGO=cargo-zigbuild` (ROADMAP
  O41), both shipped in v0.22.0; they replace media_server's `xargs -P 4` loop.
- **routines, ztools, monitor:** drop the plain `cargo test` step (keep `--doc`); P1c's gate side
  landed in v0.21.0.
- **antiknob / divoom:** `tools/lock_guard.sh` is redundant with the gate; retire it. CI installs
  from `python3 $GOH_DIR/gates/required_tools.py --repo . --install`.
- **ztools:** HEAD (`40148ae`) is RED on its own code under clippy 1.99 (72 `assert_is_empty`); its
  hooks differ textually (`install.sh --force` is ztools' call); write `GOH_EXCLUDE='^vendor/'`.
- **ZoneWM:** `check_probes_pass.py` discovery stays by `check_*` NAME (`input_lock.py --probe` would
  grab the real keyboard) and names the self-proofs it does not run.
- **Finance:** `tests/test_repos.sh:113`, `tests/test_one_plan_of_record.sh:214` write
  `"$root.out"` beside the repo root; `push_gate.sh` removes and names it; the fix is Finance's.
- **Over the line cap since build files entered scope:** `CadGoose/CMakeLists.txt` (702),
  `games/CadGoose2/CMakeLists.txt` (1142), `games/necrohand/Makefile` (511).
- **games/ZeroThunder:** `tests/e2e/garden_drag_flicker.py:73` drops its process handle on the
  `wait_for_app` failure path; the gate is red on it. Unfixed because it needs a real screen.
- **Every consumer pushing by branch name over HTTPS:** `push_gate.sh` refuses a ref that moved
  while it was gated; ZoneWM's pinned `git push <remote> <sha>:<branch>` is the airtight form.
- **O35 (servers):** a per-consumer `gate_calibration.json` registry; a feature here, unscoped.

## Known limits, stated so they are not rediscovered

### `goh unreaped-spawn` + `lib/orphan_canary.py`

- A child that calls `setsid()` leaves the step's process group, the canary's only evidence
  (`ppid == 1` cross-reported every concurrent gate). Needs its own signal, e.g. a helper pidfile.
- Per-function, not interprocedural: a spawn RETURNED is a handoff (four in the estate). Same-file
  callees that can panic are read as panicking; other files and crates are not.
- Cross-CRATE guard types are reported `unjudgeable`, counted and named, never failed on.
- A kill inside a conditional (`if cond { child.kill(); }`) is accepted. Cost unknown; needs a
  corpus.
- Rust, Python, shell only. No `.swift`/`.ts`/`.go` test spawns in the estate today; re-measure.

### SUPERSOTA residuals (`docs/SUPERSOTA.md` §3)

- R3: `check_estate_corpus.py` proves a checker still REFUSES a plant; it cannot prove the checker
  is correct, and consumer-side checks are unmeasured.

### The frozen spec

- `tests/reference_kit.py` runs the retired Python from commit `96018bd`; a clone without that
  history (shallow) cannot run the parity suites and is refused by name. The spec is never
  re-pinned to a newer commit: a deliberate behaviour change is written into the native test that
  owns it.

## Deferred, each with its re-open condition

- Secrets v2 (entropy): measure the FP rate on all consumer trees before enforcing.
- shfmt: only as reformat-everything, or a flag set proven clean on this tree.
- Swift coverage merge: needs a real-swift oracle across Xcode versions.
- Periodic disk watch: `scripts/bin/disk_hygiene.sh --warn-only` wants a launchd plist.
- `secret-ok` scope: line + line-above; path-scoped rules only under real fixture pressure.
- `lcov_merge` native port: re-open when a merge measures > 1 s (largest today: 76 ms).
- The remaining Python gate-side tools (`check_baseline_ratchet`, `check_display_seam`,
  `check_generated_fresh`, `check_tests_registered`, `check_swift_*`) stay Python: a consumer runs
  each with arguments and no structural step runs them. Re-open when one measures on a hot path.
- The `--full` sweeps (`check_empty_scope.py`, `check_probes_pass.py`) stay Python by SUBJECT: they
  run a repo's own Python gates. Re-open when either costs > 1 s on a consumer's push.
- Credentials: `.netrc` out of scope until some gate already reads outside its repo; CI configs are
  committed, so `goh secrets` covers them. Not a gap.
