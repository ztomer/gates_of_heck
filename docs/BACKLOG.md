# BACKLOG — the roadmap, and every open item (rule #16: one forward-looking file)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-06, v0.23.0 + unreleased (read first)

v0.23.0 retired the Python checkers: `bin/goh` is the only structural tier. Main is 17 commits past
the tag (CHANGELOG `Unreleased`): the spawn cuts, one-run coverage, the learned rust scope, the C2
writer hook, `tools/session_bench.py`. **v0.24.0 is cut when every phase below is done (owner, 2026-10-06).** Box: 16 cores, 4-5 busy
at idle, sys ~= user, so **spawn count, tree walks and network round trips are the cost metric, not
CPU**. The suite runs `-n 12` (faster than 8 since the 2026-10-06 spawn cuts; 16 a draw). Every wall-clock number below needs a QUIET box (load < 4):
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
| C2 (writer half) `hooks/claude/skill_edit.sh`, a PostToolUse hook: the corpus judged when a skill is written | `5780297` | 183 ms per skill edit |
| `tools/session_bench.py`: cross-session serialization, USL-fitted | `22f5bc8` | below |
| spawn cuts: display-seam probe without git per case; `bounded_run` lean (no `ps` when the group is empty, lazy imports, `-S`); `repo_root` asked once; one `git config`; `repo` fixture copied from a template | `1278870`..`a651052` | probe 1.2 -> 0.14 s; per step 59.6 -> 26.4 ms; staged run 16 -> 9 git calls; fixture 41.9 -> 4.8 ms (x614) |
| a build's in-repo reads outside its scope are learned, not fatal (rust proven cache) | `d384c0d` | vpn-watchdog-rs: never recorded -> skipped from the 3rd run |
| rust coverage: one instrumented run of every target, exported once | `170f60b` | 29/29 media_server crates identical; 226 -> 173 s |
| git's repository-binding variables asked once per process tree (`GOH_GIT_LOCAL_VARS`) | `bd3bff7` | ~1300 fewer spawns per suite run |
| suite at `-n 12` | `83af2ac` | 74/96 s -> 67/79 s interleaved |
| P0-P4, C1, C3-C5 | v0.20.0-v0.22.0 | see CHANGELOG |

## Resume here (2026-10-06, after `7b4f79c`)

- Owner, standing for this campaign: no optimisation left on the table -- items declined on YIELD
  are reopened (4.1 done as `fast_init` for the 10 busiest files, ~200 more sites to migrate; 4C.2
  native proven cache and 4C.3 native canary/local_ci runner to DO, not decline). Declines on
  SOUNDNESS stand (2.5's probe cache).
- A temporary 8 GB RAM disk is mounted at `/Volumes/gohram` (device in `/tmp/gohram.dev`) for this
  campaign's compiles and suite (`TMPDIR=/Volumes/gohram/tmp`, `CARGO_TARGET_DIR=/Volumes/gohram/target`);
  eject it (`diskutil eject`) when the campaign ends -- not a standing setup.
- Main (`~/Projects/gates_of_heck`) is at `b174dba`. Fast-forward it only after this repo's
  own push gate is green on the branch tip (`tools/gate_profile.sh .` runs it on HEAD in an
  export).
- Every Rust change runs `cargo test --workspace` before its commit (3b94809 shipped a red one).
- 5B drift cases seen so far: a test rebuilding goh without `GOH_BIN`, a whole gate run to check one
  step, per-site `git init`, a test moving the checkout, pools sized to `os.cpu_count()`, a leaked
  child holding a captured pipe (30 s), and timing thresholds that flake under load
  (`test_local_ci_jobs.py::test_jobs_flag_wins_over_gatesrc`, 1.11 s against 1.1 s).

## Roadmap to v0.24.0 — the plan of record

The owner's rule (2026-10-06): every item below is DONE before the release is cut. Done means the
item's exit number met, or -- only where the work belongs to another repo or session -- handed to
its owner with the evidence and recorded here. Phases run in order; within a phase, any order.
Status: `[ ]` open, `[x]` done (with the commit), `[~]` handed off.

**Phase 1 — correctness: a gate that says more than it does** (details: "Open -- found", 1-2)
- [x] 1.1 Rust per-target coverage floors are inert: refuse a floors file whose target floors the
      rust mode cannot apply, naming the key (red-first: `{"covfix": 100}` over 50% passes today).
      Both shapes refused, exit 2 (`22c8d49`).
- [x] 1.3 (found by ztools' notes) a rust `exempt` key matched only the ABSOLUTE SF path, so it held
      at one checkout path and read "stale" in a push gate's export: a key relative to the project
      now names the same file anywhere (`22c8d49`).
- [x] 1.4 (same) an unreadable floors file was a warning and a pass on `--floor` alone, the per-file
      check silently dropped: refused, exit 2 (`22c8d49`).
- [x] 1.2 The HEAD export cache is bounded by LAST USE and count; an export used within the hour is
      never removed (the old prune went by creation time, after a week, with no count bound)
      (`2517e42`).

**Phase 2 — fewer spawns in every gate** (details: "Open -- found", 4, 6, 7)
- [x] 2.1 `goh.sh` resolves the binary once per process tree (exported, validated); ratchet: a
      `goh.sh` call inside a gate spawns no git. Child call 78 -> 34 ms; `rust_gate.sh` resolves
      once for its four `goh.sh` steps (`ae8b768`).
- [x] 2.2 `check_estate_corpus`: scratch repos from a template with the identity on the commit
      (one shared `_gitutil.scratch_git`, the display-seam probe's copy folded in): 90 -> 45 git
      calls per run (`c4e3dc3`). Its pool, MEASURED: 8 vs 4 workers alone 1.42 vs 1.73 s, under 4
      concurrent sessions 7.3 vs 7.3 s -- the pool is not the serializer, so it stays. One run is
      4.6 CPU-s in 2.1 s wall, 3.35 s of it sys: ~4000 file creations (the corpus copies and the
      objects `git add` writes), which contend across processes. That is 2.4.
- [x] 2.4 A verdict cache for `check_estate_corpus`, so the corpus is materialised only when an
      input moved. Key: each entry's source scope at HEAD and any uncommitted edit under it, the
      checker's own source, the `goh` binary's stamp. Every way a hit can be wrong, tested BEFORE
      the cache: a committed edit in a scope, an uncommitted one, a new checker binary, an edited
      checker, an estate repo gone (never a hit: "unavailable"), a corrupt entry. Exit: a repeat run
      with nothing moved materialises nothing (counted), and phase 6.2's sigma. Done (`3ede8a3`):
      a warm run 1.49 -> 0.17 s, sys 3.86 -> 0.43 s; eleven ways-to-lie tests.
- [x] 2.3 `lib/orphan_canary.py` sheds `dataclasses` and loads `json` only to write a snapshot
      (`6f6110a`). A/B per step, 25 interleaved: 44.3 -> 43.0 ms -- measured, and smaller than
      expected: the rest is the C4 stanza's `subprocess` import (needed anyway) and argparse, ~25 ms
      per push across local_ci's five steps; a second hand parser is not worth that.

- [x] 2.5 (beyond the targets, owner 2026-10-06) the self-proofs: `gate self-proofs still pass`
      (`check_probes_pass`) is now the largest step of this repo's `structural --full` (2.5 s of
      ~3.6; `check_empty_scope --probe` alone 1.8 s). A self-proof's verdict depends on CODE, not
      on the tree: cache each probe's pass on the gates export's commit (an immutable tree, C4) or,
      under GOH_LIVE, the probe's source and everything it imports, plus the binary and GOH_*.
      Ways to lie tested first, as 2.4. MEASURED AND DECLINED: a probe may read its repo's own
      tree, and nothing records what a Python probe read, so a gate-dir key could lie and a
      whole-tree key saves nothing the proven cache does not already skip; the slowest probe
      (`check_empty_scope`, 1.8 s) is a sequential story over one fixture repo whose sweeps share
      a git index (not safe to parallelise), and 1 s of it is the hang case's timeout, already
      the floor that does not flake under load. This repo only; consumers run their own probes.

**Phase 3 — coverage without the clean rebuild** (details: "Open -- found", 3)
- [x] 3.1 An incremental instrumented build whose report counts ONLY the current build's objects;
      each way it can lie (a deleted/renamed test binary, a previous build's profile) tested first;
      the 29-crate A/B shows identical reports. Done (`06afca4`): only the PROFILES are reset;
      29/29 crates identical to the clean build, 173 -> 76 s. It also exposed the gate WRITING a
      missing Cargo.lock as a side effect of the clean (a fixture relied on it).

**Phase 4 — the suite <= 60 s** (details: "Open -- found", 5)
- [x] 4.1 A shared empty-repo template for the 113 test sites that `git init` (~500 spawns).
      MEASURED AND DECLINED: 658 `git init`s per run, spread thin (50 in the busiest file);
      the template saves ~13 ms each -- ~8.5 s summed, < 1 s of wall -- not worth a 113-site
      diff. Template copying itself is 1 ms of the 18 ms (`GIT_TEMPLATE_DIR` empty: 16.9 ms).
- [x] 4.2 The `release` xdist group deleted (its reason is gone since `31bbb81`); three suite
      runs green after it, 62-65 s (`2642cbe`).
- [x] 4.3 Fewer processes per test in the heaviest files (`test_gate_environment.py`,
      `test_hook_git_env.py`, `test_rust_gate_scoped_cache.py`, `test_proven.py`) without
      weakening what each proves. Done: the gate-environment push rebuilt goh behind the session
      (10.2 -> 1.7 s, `922f21e`); a cargo shim over a whole run found no other. What is left in
      the heavy files is gate START-UP cost per run -- phase 4C's subject.

- [x] 4.4 (owner's question, 2026-10-06) a RAM disk for the suite's temp files: MEASURED, 4
      interleaved pairs, `TMPDIR` on a 2 GB APFS RAM disk vs the SSD: 59/65/59/60 s vs 71/64/64/67 s,
      ~8% faster. It does NOTHING for cross-session contention (git fixture churn, 8 sessions: 5.0 s
      on both, sigma 0.09 on both) -- that is kernel metadata, not I/O. `TMPDIR` already selects it;
      whether to keep 2 GB of RAM mounted for it is the owner's call (asked). Owner: use one for
      this campaign's compiles and goh runs (also spares the SSD), ejected at its end -- not a
      standing setup.

**Phase 4C — the orchestration in Rust, where shell is the wrong tool** (owner, 2026-10-06:
"nothing forces us to stay on shell")
Every lever left is the cost of STARTING something: a bash, its sourced libs, a Python wrapper,
the same git question per process. One native process holding the answers removes the class.
Baselines (2026-10-06, after phases 2-3): a one-step `local_ci.sh` run 526 ms and 21 git calls
(the proven key twice, the gates identity per key); the step wrapper 26 ms a step; a `goh.sh`
child 34 ms; `structural.sh --staged` 0.22 s (media_server). Ports in order of yield, each
behind the same tests the shell passes today, red-proven, one at a time:
- [x] 4C.1 `goh step`: the ceiling wrapper native (process group, TERM->grace->KILL sweep, the
      leak sample, GOH_TIMINGS). Its one blocker -- a signal ignored at entry must stay ignored --
      needs the inherited disposition (`sigaction`); `unsafe` is denied in the crate, so either a
      vetted crate exposing it safely, or one exemption the unsafe allowlist names, with a test.
      Done (`3b94809`): `crates/goh-sys` (the one allowlisted `unsafe`) + `signal-hook`; the CLI
      contract pinned against BOTH wrappers (15 cases each); 26-79 ms -> 4.3 ms a step.
- [x] 4C.2 The proven cache native (`goh proven key|lookup|record`): REOPENED (owner) and DONE
      (`23976e1`). Byte-identical keys and the bash record format, so either side reads the other's
      records (`tests/test_proven_native.py`); the identity's constant half from the bash memo,
      its live half (builtins) on stdin. Key + lookup 39.2 -> 30.3 ms; earlier bash cuts
      `54e8d83`/`4c0d4f3`-era: identity once per tree, no needless git/date spawns.
- [x] 4C.3 `local_ci.sh`'s step runner native: parallel steps under the canary, logs, proven
      records -- one process instead of a bash + a Python wrapper per step. REOPENED (owner: no optimisation left
      on the table) and DONE (`8edb913`): `goh canary`, the canary's contract pinned against both
      implementations; `local_ci.sh` resolves the binary once and runs every step under it.
- [x] 4C.4 Re-measured after 4C: a step wrapper 4.3 ms (native), a `goh.sh` child 34 ms (resolved
      once), a warm proven hit 6 git calls, the suite 57-62 s (RAM-disk temp, load 9-15).

**Phase 5 — known limits worth closing**
- [x] 5.1 The C2 writer hook also judges a skill edited through Bash (a PostToolUse `Bash` matcher
      that fires when the command names the corpus root). Done (`4919960`): the matcher is
      `Write|Edit|MultiEdit|Bash`, and a builtin-only fast path exits before Python on an event
      that does not name the corpus's directory -- 7 ms on an unrelated Bash command.

**Phase 5B — the suite cannot drift back** (owner, 2026-10-06: after every optimisation above)
- [x] 5B.1 The suite cannot drift back. Chosen by what can fail WITHOUT flaking -- a count or a
      refusal, never a timing budget, since every timing threshold this campaign flaked under load:
      `tests/_drift_guard.py` puts a `cargo` shim first on every worker's PATH that refuses a goh
      build unless the caller says `DRIFT_BUILD_OK=1` (the session fixture; a test whose subject is
      the build), and fails any test whose call phase passes 60 s (`SLOW` names the real builds);
      `tests/test_suite_drift.py` ratchets `git init` sites per test file and `cpu_count()` pools,
      and plants a slow test and a goh build in a child pytest to prove both guards go red;
      `--strict-markers` (pyproject) makes an unregistered mark an error. First run caught two:
      under GOH_LIVE with uncommitted Rust, ~75 gate tests each built `target/goh-live` (now built
      once, before the workers start), and conftest's own `pytest_configure` shadowed by an import
      (the `slow` mark went unregistered, silently). The flake it surfaced was a real race, fixed:
      a stop landing while `Popen` was still returning orphaned the step, and a second Ctrl-C
      abandoned a sweep -- `bounded_run.py` now holds both; `goh step` listens before it spawns.

**Phase 6 — the measurements, on a quiet box (load < 4)** (details: "Open -- measurements")
- [ ] 6.1 This repo's suite <= 60 s.
- [ ] 6.2 Cross-session sigma for `structural --full` <= 0.15 (`tools/session_bench.py`).
- [ ] 6.3 media_server push, everything changed: gates_of_heck's share (the rust phase) measured
      against its 90 s target with media_server's own pytest step separated out.
- [ ] 6.4 media_server push, one crate changed: same split, against 30 s.
- [ ] 6.5 The P2 `GOH_CI_JOBS` budget curve (1/2/4/6), its knee recorded.

**Phase 7 — downstream: each consumer item to its owner** (details: "Downstream")
- [ ] 7.1 Each item below sent to the session that owns the repo, with the evidence; marked `[~]`
      here with the date. (servers: the pytest step outside the proven cache, `--each-crate`;
      monitor, antiknob, divoom, routines, ztools, ZoneWM, Finance, ZeroThunder, the line-cap repos.)

**Phase 8 — the "fix the class" commit gate** (details: its own section)
- [ ] 8.1 Land ZoneWM's handed-over design here, test-first (red-proven both directions), once its
      prototype has the real-history data it promised.

**Phase 9 — release**
- [ ] 9.1 v0.24.0: version bump, CHANGELOG `Unreleased` -> `v0.24.0`, full gate green, tag, push,
      GitHub release (`tools/release-kit/release.sh`); then tell servers the tag.

## Open — measurements (each needs a quiet box)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands.

| what | baseline | last | target |
|---|---|---|---|
| this repo's suite, `-n 12 --dist loadgroup` | 94.4 s (v0.20, `-n 8`) | **66.9 s from a quiet start (load 3.6, 2026-10-06)** at `-n 12`; 67-79 s at load 9-56 (`-n 8` 74-96 s interleaved). Workers packed 52-64 s busy each, summed 663 s | <= 60 s: misses by ~7 s |
| media_server push, one crate changed, warm | 197 s | 183 s at load 7-12 (2026-10-06): **110 s is media_server's own pytest suite** (134 tests, run by its gate.sh outside the proven cache); 5 of 29 crates re-gated -- 3 correctly (path users), healthcheck-rs on the whole tree by design (its tests read the repo root), vpn-watchdog-rs never recorded (fixed in `d384c0d`) | <= 30 s: unreachable from here while the pytest step runs unconditionally -- a servers item (below) |
| media_server push, everything changed, warm | 177 s (P0) | 170 s at load 4-8 (2026-10-06): coverage 269 of ~360 summed step-s (29 crates, each rebuilt instrumented from clean) | <= 90 s: lever is the coverage rebuild |
| any consumer's pre-commit structural layer | ~1 s | media_server, one staged `.rs`: 0.22 s at load 17 (2026-10-06) | <= 0.4 s: **MET** |
| P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines | 186/147/138 s (1/2/4, busy) | provisional | the knee, recorded |

It misses by ~7 s, and what is left is the suite's own contention, not waste: a nested pytest that
takes 0.7 s alone takes 16 s inside the run (`test_conftest_scrubs_before_any_fixture_runs`) --
process start-up under twelve workers' sys load. The lever is FEWER PROCESSES PER TEST (a test
that runs a whole gate to check one step), not faster ones. Levers by summed time (2026-10-06): `test_rust_gate_scoped_cache.py` ~45 s and
`test_rust_gate.py` ~28 s of real cargo, `test_claim_derivation_native_parity.py` ~21 s (each case
runs the frozen Python reference -- the spec, not tunable), `test_proven.py` ~21 s,
`test_swift_gate_baseline.py` ~21 s (13 s is the one real-swiftlint test). Every gate a test runs
pays `goh.sh` resolution (31 ms from the export, 62 ms from the checkout) and per-step wrapper
cost (now 26 ms); those are the broad levers left. On this box wall clock moved +/-25% run to run
with other sessions' xctest, so the exit needs a quiet window; judge changes by A/B and by counts.

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
workers) was unreadable at load 18-83; it needs the quiet box like every row above.

## Open — found 2026-10-06, each sized and ready to start

Defects first (a gate that says more than it does), then levers by expected yield.

1. **Rust per-target coverage floors are INERT.** `lcov_merge.py::_load_floors` reads a flat
   `{target: floor}` file into `targets` and nothing applies it -- only `coverage_swift.py` does. A
   rust consumer writing per-target floors is told nothing and gated on nothing (Rust rule #6, "no
   inert config that reads like a gate"). Since `170f60b` the rust mode has ONE part, so a
   per-target floor is not even measurable there. Exit: the rust mode REFUSES a floors file with
   target floors (naming the key and the reason), or the feature is built with per-target parts;
   refusing is the honest default. Red-first: a rust run with `{"covfix": 100}` over 50% coverage
   passes today.
2. **The HEAD export cache was pruned by CREATION time, unbounded in count.** An export older than
   a week went even while a long-lived worktree still ran from it, and 55 exports, 226 MB built up
   in that week (2026-10-06). Done: phase 1.2.
   Exit: bounded by count or age with the CURRENT export never removed; a pruner that races a gate
   reading an export is the way it lies -- test that an export in use survives (a lock or an mtime
   touched on use).
3. **The media_server everything-changed push rebuilds every crate instrumented from clean**
   (`cargo llvm-cov clean --workspace` at the start of each coverage run: 225 of ~330 summed step-s
   are coverage). The clean exists because stale instrumented artifacts merged into a report (93.5%
   read for 96%, routines 2026-09-21). Exit: an incremental instrumented build whose report counts
   ONLY the current build's objects; lies: a renamed/deleted test binary's stale object, a
   profile of a previous build. Test each before the change; target <= 90 s for the push.
4. **Every `goh.sh` call re-resolves the binary**: 31 ms from the export, 62 ms from the checkout,
   and under `GOH_LIVE` six git calls for the working-tree delta. One resolution per process tree
   (an exported, validated answer -- the `GOH_GIT_LOCAL_VARS` pattern) removes most of it. Lies: a
   binary rebuilt mid-tree, a delta that changed mid-run (the tree guard already fails a run that
   moves the checkout). Exit: a `goh.sh` call inside a gate spawns no git (shim-counted ratchet).
5. **The suite's last ~7 s (66.9 s quiet against 60 s).** Fewer processes per test: the heaviest
   tests run a whole gate to check one step (`test_gate_environment.py` ~15 s,
   `test_hook_git_env.py::test_conftest_scrubs...` 0.7 s alone / 16 s in-suite,
   `test_rust_gate_scoped_cache.py` ~45 s of real cargo). Small, mechanical: 113 test sites `git
   init` a fresh repo each (a shared template helper, as `repo` got: ~500 spawns); the `release`
   xdist group's reason is gone since `31bbb81` (the hardening test rewrites a copy), so the group
   only constrains packing -- delete it, and keep the tree guard as the proof.
6. **`check_estate_corpus` saturates the box inside `structural --full`** (sigma 0.84 alone): 16
   scratch repos per run, 5 git spawns each (init, config x2, add, commit -> template + `-c`
   identity = 2), a pool of 8 threads sized as if the machine were idle, 24 `goh.sh` calls (item
   4). Exit: the cross-session sigma <= 0.15 above, measured on a quiet box.
7. **`lib/orphan_canary.py` still imports `dataclasses` and `argparse`** on every `local_ci.sh`
   step; the `bounded_run.py` treatment (`7dfb65c`) applies. Small.

## Open — "fix the class" commit gate (requested by ZoneWM, owner-approved to roadmap, 2026-10-06)

The metarule is written down and was still applied one site at a time (ZoneWM, one night: window
pools hand-rolled in 4 overlays, a verify-commit-push chain broken 3 ways, one lock path in 5
files). A mechanism, in three parts, here so every repo gets it with zero re-installs:
1. a commit-msg gate: every `fix:`/`perf:` commit carries `Class: <the invariant that broke>` and
   `Siblings: <sites fixed here, or filed by roadmap id> | none (<the search that found none>)`;
   a missing trailer, or `none` without its search, is refused;
2. a repeat detector over those trailers: a Class matching 2+ earlier commits (fuzzy) is refused
   unless the commit is the systemic fix (a shared helper, a gate, a type) or cites the item that is;
3. a loop-start audit: cluster recent commits by Class and touched-file family, each cluster a
   candidate hardening item (catches repeats committed without trailers).
**Blocked on:** ZoneWM's repo-local prototype and its red/green results against its 2026-10-05/06
history; it hands the design over, then 1 lands here test-first (red-proven both directions).

## Downstream: what each consumer session needs to know

- **Every repo, after v0.24.0 (nothing to do):** rust coverage runs once per crate (identical
  reports, ~23% faster); a crate whose build reads in-repo files outside its scope is recorded
  from its second run; the step wrapper costs half. Tell servers when it is tagged.
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
  (`rust/src/ztools/twitter/native.rs`), valid in a push export too; its `_note` on inert
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
