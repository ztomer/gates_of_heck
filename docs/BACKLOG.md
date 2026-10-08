# BACKLOG — the roadmap, and every open item (rule #16: one forward-looking file)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-08, v0.24.0 (read first)

v0.24.0 closed the roadmap that followed v0.23.0 (every phase, 1-10; the plan of record is in git
history at `9241ecc`, its detail in the CHANGELOG stanza): the spawn cuts, one-run coverage, the
learned rust scope, the C2 writer hook, `tools/session_bench.py`, the suite's drift guards,
`goh commit-class` calibrated on ZoneWM's history, `gates/round.sh`, and the shared temp dir kept
claimable. Box: 16 cores, 4-5 busy at idle, sys ~= user, so **spawn count, tree walks and network
round trips are the cost metric, not CPU**. The suite runs `-n 12`. Every wall-clock number needs a
QUIET box (load < 4). Landed plans are pruned to this table.

| landed | commit | measured |
|---|---|---|
| N1 every delegated checker native (unreaped-spawn, provenance, kill-by-name, claims, md-links, lock-version, credential-urls, python-formatted, shell-lint, deps, empty-assert, tag-version) | `4707bc4`..`079fda3` | media_server `structural --full` 4.9 -> 1.9 s; unreaped-spawn 2.87 -> 0.13 s |
| P4 per-blob verdict cache for shell lint | `3e4f2e9` | media_server `--full` 1.9 -> 0.30 s warm (target 1.5 s: MET) |
| N3 stage A: the binary is the only tier; `.gatesrc` the only pipeline config | `96018bd` | -- |
| N2 + N3 stage B: ported Python deleted, the spec frozen at `96018bd` | `a6f8f33` | suite 2090 tests ~116-140 s -> 1806 ~90 s (both under load) |
| R5 a new vendored copy of a house checker refused at commit | `b4046e3` | -- |
| P5 (work) no hidden cargo builds, no twice-run tiers | `916a37a` | summed test time 693 -> ~620 s under load |
| C2 (gate half) an external corpus judged at its commit | `46cf078` | -- |
| C2 (writer half) `hooks/claude/skill_edit.sh`, a PostToolUse hook: the corpus judged when a skill is written | `5780297` | 183 ms per skill edit |
| `tools/session_bench.py`: cross-session serialization, USL-fitted | `22f5bc8` | below |
| spawn cuts: display-seam probe without git per case; `bounded_run` lean (no `ps` when the group is empty, lazy imports, `-S`); `repo_root` asked once; one `git config`; `repo` fixture copied from a template | `1278870`..`a651052` | probe 1.2 -> 0.14 s; per step 59.6 -> 26.4 ms; staged run 16 -> 9 git calls; fixture 41.9 -> 4.8 ms (x614) |
| a build's in-repo reads outside its scope are learned, not fatal (rust proven cache) | `d384c0d` | vpn-watchdog-rs: never recorded -> skipped from the 3rd run |
| rust coverage: one instrumented run of every target, exported once | `170f60b` | 29/29 media_server crates identical; 226 -> 173 s |
| git's repository-binding variables asked once per process tree (`GOH_GIT_LOCAL_VARS`) | `bd3bff7` | ~1300 fewer spawns per suite run |
| suite at `-n 12` | `83af2ac` | 74/96 s -> 67/79 s interleaved |
| rust per-target coverage floors refused, never silently inert | `22c8d49` | -- |
| rust coverage incremental; only the profiles reset | `06afca4` | coverage 173 -> 76 s |
| `goh.sh` resolves the binary once per process tree | `ae8b768` | a child call 78 -> 34 ms |
| the suite's `release` xdist group gone | `2642cbe` | -- |
| estate corpus remembers a verified entry | `3ede8a3` | warm run 1.49 -> 0.17 s, sys 3.86 -> 0.43 s |
| `goh canary`: local_ci's step runner native; `fast_init` everywhere | `8edb913` | -- |
| `goh commit-class` calibrated on ZoneWM's history | `9241ecc` | 1 -> 6 of 13 same-class pairs, 2 of 1,418 false |
| every temp in the shared dir claimable; the suite sweeps it | `cee397b` | ~1,470 stranded -> 14 |
| C++ coverage merge bounded (`%8m`, `--input-files`), this run only | `6350c42`, `3ab524c` | -- |
| P0-P4, C1, C3-C5 | v0.20.0-v0.22.0 | see CHANGELOG |

## Resume here (2026-10-08, after v0.24.0)

- Every Rust change runs `cargo test --workspace` before its commit (3b94809 shipped a red one).
- Main (`~/Projects/gates_of_heck`) is fast-forwarded only after this repo's own push gate is
  green on the branch tip (`tools/gate_profile.sh .` runs it on HEAD in an export).
- Consumers told of the tag: servers and ztools only (the owner's call, 2026-10-08, for quota).
  ZoneWM was told on the owner's word (2026-10-08) and puts its switch to the stock commit-msg
  hook (`GOH_COMMIT_CLASS=1`) to its owner. ztools had no session open: not yet told.

## Roadmap to v0.25.0 — the plan of record

Every item carries its **Baseline:**, **Exit:**, **Red-first:** and **Lies:**, and a done item
names its commit, or `tests/test_backlog_items.py` fails. A landed item moves to the State table.
Phases run in order; within a phase, any order. `[ ]` open, `[x]` done, `[~]` handed off.

**Phase 1 — re-measure what v0.24.0 claims, on a quiet box (load < 4, recorded beside each number)**
Each runs under `tools/quiet.sh --` (every gate on the host held off, the desktop held, load < 4).
- [ ] 1.1 Cross-session serialization of `structural --full` (`tools/session_bench.py`, N=1/2/4/8).
      Baseline: sigma 0.45 at load 7-18, BEFORE the estate cache (`3ede8a3`) landed. Exit: sigma
      <= 0.15, or the step that holds it named by `session_bench`'s per-step inflation. Red-first:
      the bench on a deliberately serialized control (one `flock`ed step) reports sigma near 1, or
      the instrument is blind. Lies: other sessions' load (record it); a warm cache measured as cold.
- [ ] 1.2 media_server push, everything changed, warm. Baseline: 170 s at load 4-8, BEFORE the
      incremental coverage build (`06afca4`: 173 -> 76 s on this repo). Exit: <= 90 s. Red-first:
      the P0 instrument's per-step sum within 5% of wall on the same run. Lies: a cold sccache; a
      crate the "everything changed" diff did not touch; media_server's own 110 s pytest step
      (a servers item) counted as goh's.
- [ ] 1.3 The P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines. Baseline: 186/147/138
      s (1/2/4, a busy box). Exit: the knee recorded, and `GOH_CI_JOBS`' default set at it.
      Red-first: a run at jobs=1 is no faster than jobs=4, or the steps are serial somewhere.
      Lies: one repo's knee read as every repo's.

**Phase 2 — `goh requires-call`** (ZoneWM, 2026-10-08; owner: "generic tooling goes to goh")
- [ ] 2.1 A Python parser in the binary, sized. Baseline: none in the crate today; ZoneWM's
      checkers use `ast`. Exit: one crate chosen (candidates: `ruff_python_parser`,
      `rustpython-parser`, `tree-sitter-python`) with its build-time and binary-size cost measured,
      `cargo audit` clean, one version in the lock. Red-first: the call sites it finds in ZoneWM's
      `tools/*.py` equal Python `ast`'s, byte for byte. Lies: a newer syntax (3.14) parsed
      differently -- test the estate's own Python, not a sample.
- [ ] 2.2 The check: "a file that CALLS X must also CALL Y", rows in `.gatesrc`. Baseline: two
      repo-local ZoneWM gates of this shape (`tools/check_probe_courtesy.py`,
      `tools/check_probe_placement.py`, 170 lines, an 8-case selftest). Exit: both expressible as
      rows, the 8 cases pass native, ZoneWM's copies deletable (its owner's call). Red-first: each
      case red-proven -- a docstring naming Y does not satisfy; Y reached through an import alias
      does, another module's same-named function does not; an exemption goes STALE three ways (file
      gone, no longer calls X, now calls Y); zero X-callers fails the floor. Lies: `getattr` and
      star imports (named as limits, not passed).

**Phase 3 — "fix the class", part 3: the clustering audit**
- [ ] 3.1 `goh commit-class --clusters REV..`: cluster commits by Class AND touched-file family, each
      cluster a hardening candidate. Baseline: the word measure sees 6 of ZoneWM's 13 labelled
      pairs; repeats committed without trailers are seen by nothing. Exit: over ZoneWM's
      `214f0cf7..HEAD` it names its author's clusters 1-6 with at most 2 spurious clusters.
      Red-first: the labelled list (`9241ecc`'s data) as the fixture, run against the current
      rule first. Lies: a file every commit touches (a Makefile, a CHANGELOG) linking everything --
      files touched by more than a quarter of the range do not count.
      A further labelled pair (ZoneWM, 2026-10-08): `30b74237` "a capture taken after a fixed
      sleep, racing the asynchronous work whose result it captures" with `a9604cf3` -- one shared
      word, a rephrasing.
- [ ] 3.2 Domain-frequent words do not count. Baseline: the full replay's one wrong refusal,
      `078f137f`, shares only "read" and "window", ZoneWM's own domain nouns. Exit: words in more
      than a set share of a repo's own classes are dropped per repo; the replay keeps its 3 right
      refusals and loses `078f137f`, and the 6 of 13 labelled pairs stay. Red-first: `078f137f`
      refused by today's rule, as the fixture. Lies: a small history, where every word is
      "frequent" -- below a minimum class count the list is empty.

**Phase 4 — downstream, verified at each repo's HEAD**
- [ ] 4.1 Every "Downstream" item below checked against its repo's current HEAD, then pruned or
      re-stated. Baseline: 17 items, last verified 2026-10-06. Exit: each carries the sha it was
      verified at, or is gone. Red-first: none -- coordination, no code; the sha stamp is the
      check. Lies: an item marked fixed from a session's word, not the repo's HEAD. Messages go
      out only where the owner says (servers and ztools on a release; quota, 2026-10-08).

**Phase 5 — release**
- [ ] 5.1 v0.25.0. Baseline: v0.24.0 (`3ab524c`). Exit: version bump, CHANGELOG `Unreleased` ->
      `v0.25.0`, full gate green on the tag commit, tag, push, GitHub release; servers and ztools
      told. Red-first: `release.sh --dry-run` prints no shell error (`c9e3939`). Lies: a gate run
      on a different commit than the one tagged.

## Open — measurements (each needs a quiet box)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands.

| what | baseline | last | target |
|---|---|---|---|
| this repo's suite, `-n 12 --dist loadgroup` | 94.4 s (v0.20, `-n 8`) | **52.5-53.8 s at load 11-17 (2026-10-06)**, 1987 tests, longest-first (`tests/_schedule.py`); 62-67 s before it on a quiet box | <= 60 s: **MET** |
| media_server push, one crate changed, warm | 197 s | 183 s at load 7-12 (2026-10-06): **110 s is media_server's own pytest suite** (134 tests, run by its gate.sh outside the proven cache); 5 of 29 crates re-gated -- 3 correctly (path users), healthcheck-rs on the whole tree by design (its tests read the repo root), vpn-watchdog-rs never recorded (fixed in `d384c0d`) | <= 30 s: unreachable from here while the pytest step runs unconditionally -- a servers item (below) |
| media_server push, everything changed, warm | 177 s (P0) | 170 s at load 4-8 (2026-10-06), before `06afca4` made the instrumented build incremental | <= 90 s: Phase 1.2 re-measures |
| any consumer's pre-commit structural layer | ~1 s | media_server, one staged `.rs`: 0.22 s at load 17 (2026-10-06) | <= 0.4 s: **MET** |
| P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines | 186/147/138 s (1/2/4, busy) | provisional | the knee, recorded |

The suite target is met; its remaining levers (fewer processes per test, `goh.sh` resolution,
the per-step wrapper) are recorded in the CHANGELOG. Every other row is Phase 1's.

## Open — cross-session serialization (`tools/session_bench.py`)

First run, 2026-10-06, this repo, `structural.sh --full`, 3 repeats, load 9-21 from other sessions:

| N | makespan | speedup | lock waits |
|---|---|---|---|
| 1 | 3.9 s | 1.00 | -- |
| 2 | 5.8 s | 1.36 | -- |
| 4 | 11.2 s | 1.41 | -- |
| 8 | 23.0 s | 1.37 | -- |

USL sigma 0.51, kappa 0.023: half of a structural run is serialized, and NO lock was waited on.
Controls on the same box scale (8 sessions: `/usr/bin/true` x300 6.3x, CPU-bound python 6.9x), so
the machine is not the limit -- the gate is: one run is 3.9 s wall for 4.3 s user + **9.0 s sys**,
and every step inflates ~8x at N=8. The empty-scope sweep is 2.2 s of the 3.9 (4.0 s sys).
Exit: sigma <= 0.15 for `structural --full` (a session costs the next one < 15% of its run).
Attribution so far: of the sweep, `check_estate_corpus` alone has sigma 0.84 (it materialises
1935 estate files into 16 scratch repos and `git add`s them; filesystem metadata) and
`check_probes_pass` 0.40; the per-step wrapper's `ps` of every process was one more machine-wide
scan per step (removed, `8df0af4`).
Re-measured after the probe and wrapper cuts (load 7-18): sigma 0.45, one run 3.96 s -- the sweep
still dominates, and at N=8 even 18 ms native steps run 13x slower, i.e. the sweep saturates the
box for everything beside it. Next lever, by count: `check_estate_corpus` materialises 16 scratch
repos per run (copy + `git init`/`config` x2/`add`/`commit` each, a pool of `len(ESTATE)` = 8
threads, sized as if the machine were idle) and runs 24 `goh.sh` calls. A pool-cap A/B (8 vs 2
workers) was unreadable at load 18-83. The estate cache (`3ede8a3`) has landed since; Phase 1.1
re-measures.

## Downstream: what each consumer session needs to know

- **Every repo, after v0.24.0 (nothing to do):** rust coverage runs once per crate (identical
  reports, ~23% faster); a crate whose build reads in-repo files outside its scope is recorded
  from its second run; the step wrapper costs half. Servers told 2026-10-08; ztools not yet.
- **Every INSTALLED repo:** re-run `$GOH_DIR/install.sh <repo>` (a repo never installed, whose
  hooks another manager owns, is refused since 2026-10-06 -- zinc); `structural.sh` names a hook that is an older
  stock. **A Rust toolchain is now required** for layer 1 (`bin/goh` is the only tier;
  `required_tools.tsv` names `cargo`, and `build-goh.sh` refuses up front without it).
- **monitor:** its CI runs `GOH_NO_NATIVE=1 bash .gates_of_heck/gates/structural.sh --full`. The key
  is retired and ignored (said once); drop it. Its customised pre-commit (`check_gate_parity.py`) and
  working-tree pre-push generation are monitor's call; its pre-commit runs the full test suite,
  which P3 lets the push reuse.
- **antiknob:** `tools/_gitutil.py` is a vendored copy of a house file; `goh structural --full` now
  names it (a NEW copy is refused at commit). Delete it and import the shared one via `PYTHONPATH`.
- **Consumers calling a Python checker by path** (any of the 22 retired: `check_no_emoji.py`,
  `check_no_secrets.py`, `check_no_home_paths.py`, ...): each still works as a forwarder to
  `goh.sh <check>` -- all of them since 2026-10-06 (two were missing, ztools). Move to
  `bash "$GOH_DIR/gates/goh.sh" <check>`. `--exclude` takes Python's look-around again.
- **servers (media_server push, 2026-10-06):** its `tools/gate.sh` step 6 runs the 134-test pytest
  suite (110 s) on every push, outside the proven cache -- the floor under the <= 30 s one-crate
  target. Route it through a proven step (local_ci `GOH_CI_STEPS`, or a scoped key on what the
  tests read) so an unchanged input set skips it. healthcheck-rs's native-check tests read the
  repo root, so it is keyed on the whole tree by design: every edit re-gates it (~40 s coverage).
- **servers:** adopt `rust_gate.sh --each-crate` and `GOH_RUST_LINT_CARGO=cargo-zigbuild` (ROADMAP
  O41), both shipped in v0.22.0; they replace media_server's `xargs -P 4` loop.
- **routines, ztools, monitor:** drop the plain `cargo test` step (keep `--doc`); P1c's gate side
  landed in v0.21.0.
- **antiknob / divoom:** `tools/lock_guard.sh` is redundant with the gate; retire it. CI installs
  from `python3 $GOH_DIR/gates/required_tools.py --repo . --install`.
- **ztools:** `tools/coverage_floors.jsonc`'s exempt key can be repo-relative now
  (project-relative: `src/ztools/twitter/native.rs` for project `rust/` -- the first
  handoff said `rust/...`, wrong, corrected 2026-10-06), valid in a push export too; its `_note` on inert
  per-target floors and unreadable files is answered (both refused since v0.24.0).
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

### The C2 writer hook and the commit gate

- `hooks/claude/skill_edit.sh` judges a Bash command that NAMES the corpus (5.1); one that writes
  it without naming it (a `cd` there first, a variable) is judged at the next commit or push.
- The commit gate is the STAGED structural layer by design; the suite runs at push. Two commits
  of 2026-10-06 broke the suite and were caught there (`8322984`): run the suite before committing
  gate or test changes.

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
- A native `goh step` (the ceiling wrapper in Rust, ~3 ms against 26 ms): `nohup`'s contract --
  a signal ignored at entry stays ignored -- needs the inherited disposition, which only
  `sigaction` reads, and `unsafe` is denied in the crate. Re-open with a vetted crate that
  exposes the query safely, or a per-site exemption the unsafe allowlist names.
- One `rev-parse --show-toplevel` per delegated Python checker process (two per `--full`): a
  cross-process answer was judged not worth its staleness risk for two spawns. Re-open if a
  shim count shows more.
- Credentials: `.netrc` out of scope until some gate already reads outside its repo; CI configs are
  committed, so `goh secrets` covers them. Not a gap.
