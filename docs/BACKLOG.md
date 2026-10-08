# BACKLOG — the roadmap, and every open item (rule #16: one forward-looking file)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-08, v0.24.0 + Phases 1-3 of v0.25.0 (read first)

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
| `tools/quiet.sh`: a quiet host on demand (every gate held off, the desktop held, load gated) | `072bc3f`, `f0aad3b` | first real run (2026-10-08): no goh gate running, load 20 from ZoneWM's `xctest` -- refused, as designed |
| self-proofs leave the tree they ran over untouched (inode, mtime, size stamped; the probe named) | `1bf8b33` | from koffee_big: a plant-and-restore raced a parallel `copytree`; no estate probe changes its tree today |
| `goh requires-call` (v0.25 1.1-1.3): "a Python file that calls X calls Y", from the AST (`ruff_python_parser`), string-argument triggers | `4474822`, `6a26828` | ZoneWM's courtesy rule as a row: 12 of its regex's 17 files bound, the 5 others name a verb only as text |
| `goh commit-class`: domain vocabulary dropped (2.2), `--clusters` (2.1), a refused git is an error | `4d9d9f9`, `c4a542e` | replay 4 -> 3 refusals, all right; clusters: 1 labelled cluster whole by words, +2 by files; 2 are paraphrase in different files, beyond words and paths |
| Downstream verified at each repo's HEAD (3.1) | `feefd2c` + this rework | 20 items: 7 done (pruned), 3 partly, 8 open, 2 general |
| P0-P4, C1, C3-C5 | v0.20.0-v0.22.0 | see CHANGELOG |

## Resume here (2026-10-08, after v0.24.0)

- Every Rust change runs `cargo test --workspace` before its commit (3b94809 shipped a red one).
- Main (`~/Projects/gates_of_heck`) is fast-forwarded only by `tools/land.sh` from the branch's
  worktree: it gates the tip (`tools/gate_profile.sh .`) and merges that SHA, or nothing.
- Consumers told of the tag: servers and ztools only (the owner's call, 2026-10-08, for quota).
  ZoneWM was told on the owner's word (2026-10-08) and puts its switch to the stock commit-msg
  hook (`GOH_COMMIT_CLASS=1`) to its owner. ztools had no session open: not yet told.
- The Phase 4 driver queued at 09:57 (`quiet.sh`, one command for all three) refuses at its 4 h
  deadline (~13:57) without having run; re-queue it per item once 3.1-3.2 land.

## Roadmap to v0.25.0 — the plan of record (re-phased 2026-10-08 12:00)

Every item carries its **Baseline:**, **Exit:**, **Red-first:** and **Lies:**, and a done item
names its commit, or `tests/test_backlog_items.py` fails. A landed item moves to the State table.
Phases run in order; within a phase, any order. `[ ]` open, `[x]` done, `[~]` handed off.
Phases 1-2 (`goh requires-call`, the commit-class vocabulary and clusters) and the downstream
sweep are landed (State table). **Why the re-phase:** the measurements were queued at 09:57 and in
2 h never ran. `tools/quiet.sh` waits for load < 4 HOLDING NOTHING, so every new gate from the
other sessions (11-12 registered at a time) starts ahead of it -- a reader-preferring queue, which
starves its writer -- and load < 4 is under this box's own idle floor (State: "4-5 busy at
idle"; macOS counts threads blocked on Spotlight's I/O as load). The queue gets fixed first.

**Phase 3 — a queue that runs (blocks Phase 4)**
- [ ] 3.1 `quiet.sh` takes its place before it waits: claim the exclusive lock first (new gates
      queue behind it, running ones drain), THEN judge the box. Baseline: 0 windows in 2 h at load
      14-50 (2026-10-08 09:57-12:00), every gate overtaking it. Exit: under a fake load that is
      high exactly while a gate is registered, the measurement runs within drain + settle, and a
      box still busy without any gate is refused, naming its busiest processes. Red-first: that
      fake against today's `quiet.sh` refuses at its deadline. Lies: a gate already running holds
      its whole run -- the drain waits for it, up to `GOH_BENCH_WAIT`.
- [ ] 3.2 A hold is short and bounded: each measurement holds the host only for its own run and
      queues again before the next, so other sessions' commits interleave; a hold has its
      expected length up front, and `GOH_BENCH_MAX_HOLD` defaults to 15 min, not 60. Baseline:
      Phase 4 queued as ONE command (all of 4.1-4.3, ~40 min); a gate waits behind a hold up to
      its 60 min max. Exit: no hold over 15 min in the Phase 4 run; a waiting gate prints the hold's
      label and expected end. Red-first: a gate behind an overdue hold proceeds at the cap, not
      after it. Lies: one chunk that needs longer -- it says so and is refused, never extended.
- [ ] 3.3 The quiet criterion calibrated, not assumed. With every gate drained, `quiet.sh` records
      the load and the controls (`session_bench`'s `/usr/bin/true` x300 and CPU-bound Python)
      before and after each run; the threshold comes from the measured floor, and a number is
      reported with its controls. Baseline: no floor ever recorded; 4 chosen by hand. Exit: the
      floor measured under a hold and written to `docs/config.md`; a run whose before/after
      controls differ by > 10% is marked noisy, not reported. Red-first: a fake control that
      doubles across a run is marked noisy. Lies: background that is not a gate (`xctest`,
      Spotlight, a cargo build run by hand) moves inside a hold -- only the controls see it.

**Phase 4 — re-measure what v0.24.0 claims (through the Phase 3 queue, one hold each)**
- [ ] 4.1 Cross-session serialization of `structural --full` (`tools/session_bench.py`, N=1/2/4/8).
      Baseline: sigma 0.45 at load 7-18, BEFORE the estate cache (`3ede8a3`) landed. Exit: sigma
      <= 0.15, or the step that holds it named by `session_bench`'s per-step inflation. Red-first:
      the bench on a deliberately serialized control (one `flock`ed step) reports sigma near 1, or
      the instrument is blind. Lies: other sessions' load (the 3.3 controls); a warm cache as cold.
- [ ] 4.2 media_server push, everything changed, warm. Baseline: 170 s at load 4-8, BEFORE the
      incremental coverage build (`06afca4`: 173 -> 76 s on this repo). Exit: <= 90 s. Red-first:
      the P0 instrument's per-step sum within 5% of wall on the same run. Lies: a cold sccache; a
      crate the "everything changed" diff did not touch; media_server's own 110 s pytest step
      (a servers item) counted as goh's.
- [ ] 4.3 The P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines. Baseline: 186/147/138
      s (1/2/4, a busy box). Exit: the knee recorded, and `GOH_CI_JOBS`' default set at it.
      Red-first: a run at jobs=1 is no faster than jobs=4, or the steps are serial somewhere.
      Lies: one repo's knee read as every repo's.

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
| media_server push, everything changed, warm | 177 s (P0) | 170 s at load 4-8 (2026-10-06), before `06afca4` made the instrumented build incremental | <= 90 s: Phase 4.2 re-measures |
| any consumer's pre-commit structural layer | ~1 s | media_server, one staged `.rs`: 0.22 s at load 17 (2026-10-06) | <= 0.4 s: **MET** |
| P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines | 186/147/138 s (1/2/4, busy) | provisional | the knee, recorded |

The suite target is met; its remaining levers (fewer processes per test, `goh.sh` resolution,
the per-step wrapper) are recorded in the CHANGELOG. Every other row is Phase 4's.

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
workers) was unreadable at load 18-83. The estate cache (`3ede8a3`) has landed since; Phase 4.1
re-measures.

## Downstream: what each consumer session needs to know

Verified read-only at each repo's committed HEAD on 2026-10-08 (the sha in brackets); an item
marked fixed from a session's word is not pruned. Done and pruned: antiknob's vendored
`_gitutil.py`, antiknob's and divoom's `lock_guard.sh`, antiknob's CI install, ztools' exempt key,
Finance's `"$root.out"`, ZeroThunder's dropped process handle.

- **Every repo, after v0.24.0 (nothing to do):** rust coverage runs once per crate; a crate whose
  build reads in-repo files outside its scope is recorded from its second run; the step wrapper
  costs half. Servers told 2026-10-08; ztools not yet.
- **Every INSTALLED repo:** re-run `$GOH_DIR/install.sh <repo>`; `structural.sh` names an older
  stock hook. At `2fb2901` (the hook-env fix) 6 of 29 matched the stock pre-commit, 23 did not;
  servers is reinstalling across the estate now -- re-check after. **A Rust toolchain is required**
  for layer 1 (`bin/goh` is the only tier).
- **Callers of a retired Python checker by path** (forwarders to `goh.sh <check>` keep working):
  ZoneWM [`d2aa402b`] `tools/release_precheck.sh:35`; app_updates [`22ec4aa2`] `tools/gate.sh:35,51`;
  koffee_big [`9e375e37`] `tools/gates_lint.sh:28,30,131`; Finance/salary [`e553bf2f`] and
  Finance/zinc-core [`972bcd47`] `tools/local_ci.sh`; games/CadGoose2 [`1a3f659d`] `.gatesrc:37`;
  games/necrohand [`fae673d3`] `Makefile:80,254`. Move to `bash "$GOH_DIR/gates/goh.sh" <check>`.
- **monitor** [`b690cfc8`]: CI still runs `GOH_NO_NATIVE=1 ... structural.sh --full` (`ci.yml:67`;
  the key is retired and ignored), and a plain `cargo test --workspace` (`.gatesrc:44`,
  `ci.yml:119`) beside the gate's. Its customised hooks are monitor's call.
- **routines** [`a4712bcc`]: `.gatesrc:26` still runs plain `cargo test --all-features --locked`
  (keep `--doc`; P1c landed in v0.21.0).
- **ztools** [`efe364a1`]: `rust/` done; `vendor/camoufox-rs` still runs a plain `cargo test`, and
  carries 24 `assert!(..is_empty())` (rust/ 1). `GOH_EXCLUDE='^vendor/'` is set. Not yet told of v0.24.0.
- **servers / media_server** [`a1a1d9ac`]: `scripts/dev/check.sh` step 6 runs the pytest suite
  (110 s) on every push outside the proven cache -- the floor under the <= 30 s one-crate target;
  route it through a proven step. `tools/gate.sh:88` still loops crates with `xargs -P 4`: adopt
  `rust_gate.sh --each-crate` and `GOH_RUST_LINT_CARGO=cargo-zigbuild` (v0.22.0).
- **divoom-control** [`0ac21228`]: CI installs with `required_tools.py --layer structural --names`
  plus apt, not `--repo . --install`.
- **ZoneWM** [`d2aa402b`]: `goh requires-call` expresses `check_probe_placement.py` and both halves
  of `check_probe_courtesy.py` as rows; `.gatesrc` sets no `GOH_REQUIRES_CALL` and both copies are
  there (5 of its 17 LEGITIMATE entries would go stale as rows). Deleting them is its owner's call.
  Its `check_focused_border_verdicts.py --probe` fails alone at that HEAD (a fill-arm assertion).
- **Over the line cap since build files entered scope:** CadGoose [`e1fa2b3c`] `CMakeLists.txt`
  (702), games/CadGoose2 [`1a3f659d`] `CMakeLists.txt` (1142), games/necrohand [`fae673d3`]
  `Makefile` (511); no exclude names them.
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
