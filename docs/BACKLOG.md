# BACKLOG — the roadmap, and every open item (rule #16: one forward-looking file)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-08, v0.25.1 (read first)

v0.25.1 is a patch over v0.25.0: the coverage scope and every path comparison are taken from the
filesystem instead of the caller's spelling, after a case-folded `--cov` scope silently dropped
every child measurement on a case-insensitive box (scripts/: 8 of 8 shell suites reporting
nothing measured, and a 0.00% number with 251 tests passing). One lesson worth carrying into the
next phase that touches measurement: **on this box, a string comparison of a path is a
measurement of the CALLER, not of the filesystem** — see the CHANGELOG stanza and
`tests/test_cwd_and_py_gate.py::test_py_gate_scopes_coverage_to_the_physical_path`.

v0.24.0 closed the roadmap that followed v0.23.0 (every phase, 1-10; the plan of record is in git
history at `9241ecc`, its detail in the CHANGELOG stanza): the spawn cuts, one-run coverage, the
learned rust scope, the C2 writer hook, `tools/session_bench.py`, the suite's drift guards,
`goh commit-class` calibrated on ZoneWM's history, `gates/round.sh`, and the shared temp dir kept
claimable. Box: 16 cores, 4-5 busy at idle, sys ~= user, so **spawn count, tree walks and network
round trips are the cost metric, not CPU**. The suite runs `-n 12`. Every wall-clock number needs a
QUIET box: through `tools/quiet.sh` (every gate held, load under 8 -- the drained floor is
4.5-6.2, measured 2026-10-08 -- and its before/after controls within 10%). **v0.25.0 ends that
assumption:** the box is never quiet for the foreseeable future (the owner, 2026-10-08; load
10-117 all evening), so a figure is a step's own work (`cpu_ms`, count) or a same-run
comparison, never a quiet wall time (4.4). Landed plans are pruned to this table.

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
| rebase picks and `git am` patches get the marker check: `hooks/post-rewrite` (reports, names `<commit>:<path>:<line>`) + `hooks/pre-applypatch` (refuses), `goh markers --commits`; `doctor.sh` checks every stock hook | `10be7e8`, `afdf661` | git 2.56: a rebase pick runs no pre-commit/commit-msg; its first real rebase (`66e638d`) named a kept `\|\|\|\|\|\|\|` base line a hand grep missed |
| 4.1 cross-session serialization of `structural --full` re-measured after the estate cache | `75f109c` (its pin) | sigma 0.45 -> 0.107 at N=1-8, under a hold |
| v0.25.0 Phase 3: the measurement queue runs -- claim before waiting, short bounded holds, the quiet floor measured, a series is not one hold | `14e7113`, `9a80351`, `310cbd3`, `f69c1fe` | a 2 h starved queue -> holds taken in minutes |
| a waiting gate holds no reader mark; a drain is bounded by its budget | `e81248e`, `10d5abf` | the drain livelock gone |
| 4.3 `GOH_CI_JOBS` curve at one pinned commit | `946daae` | knee j2: 271 -> 237 s here, 174 -> 162 s routines; the default is still 1 (the owner's call) |
| 5.1 `goh dead-after-exec` | `cf92969` | 0 findings across 30 repos |
| `goh bare-hook-index` (contract #12's reader) | `2783f0d` | routines + media_server fixed in their repos |
| the suite holds on a loaded box: no literal time bound, controls in-run, one order read | `db386fd`, `4e33f65`, `ba9a253`, `1a47f10`, `aeb5e3a` | 4 refused pushes in one evening -> 0 |
| a timed step records its CPU (`cpu_ms`) | `e3e5418` | `structural --full`, warm, at load 110-132: wall 14.2-16.7 s, cpu 7.0-7.3 s |
| P0-P4, C1, C3-C5 | v0.20.0-v0.22.0 | see CHANGELOG |

## Resume here (2026-10-08, after v0.25.0)

- Every Rust change runs `cargo test --workspace` before its commit (3b94809 shipped a red one).
- Main (`~/Projects/gates_of_heck`) is fast-forwarded only by `tools/land.sh` from the branch's
  worktree: it gates the tip (`tools/gate_profile.sh .`) and merges that SHA, or nothing.
- Consumers told of a tag: servers and ztools only (the owner's call, 2026-10-08, for quota).
  ZoneWM was told of v0.24.0 on the owner's word and puts its switch to the stock commit-msg
  hook (`GOH_COMMIT_CLASS=1`) to its owner. **v0.25.0 was told to no one** (the owner, 2026-10-08:
  out of quota); ztools has heard of neither v0.24.0 nor v0.25.0.
- No measurement waits for a quiet box any more (4.4). A hold through `tools/quiet.sh` still keeps
  goh's own gates out of a run, but its load floor is not reached on this box.

**Handoff, 2026-10-08 21:50 -- start here:**
1. v0.25.0 is tagged, pushed and released at `584afba` (origin/main). The LOCAL main checkout was
   left at `e3e5418`: uncommitted edits there that are no known session's (`gates/_common.sh`,
   `_git_env.sh`, `_proven.sh`, `push_gate.sh`, `py_gate.sh`, `tests/_drift_guard.py`,
   `tests/test_cwd_and_py_gate.py`; a `/bin/pwd -P` change and a SLOW entry among them) made
   land.sh's `merge --ff-only` refuse. Ask the owner whose they are; once committed or set aside,
   `git -C ~/Projects/gates_of_heck merge --ff-only origin/main`. Never discard them unasked.
2. servers' `push-gate-build-dir-config` (`e782f74`, worktree `~/Projects/.wt-goh-builddir`): the
   push gate's busy-path fallback exports CARGO_BUILD_BUILD_DIR, which outranks a repo's own
   `.cargo/config.toml` build-dir; the fix writes `$run_dir/.cargo/config.toml` instead, and the
   drift guard's cargo shim yields to a workspace's own build-dir. Not in v0.25.0. It edits
   `tests/_drift_guard.py`, as `584afba` does: rebase onto `584afba`, then land through land.sh.
3. 4.4's calibration is half done: one point at load 110-132 (below). The method, to repeat at a
   load <= 55: a detached worktree at a fixed SHA, `scripts/build-goh.sh` and one warm-up run in
   it, then `GOH_TIMINGS=f bin/goh step --timeout 600 --label fixed -- bash -c "cd PIN && GOH_DIR=PIN
   ./gates/structural.sh --full"`, three times, each joined to the bench lock as a gate; compare
   the spread of `ms` with `cpu_ms`. The pin's own goh must be built first, or the first sample
   times a cargo build and the wrapper is too old to write `cpu_ms`.
4. 4.3 waits on the owner: `GOH_CI_JOBS=2` per repo `.gatesrc` (recommended) or the default.

## Roadmap to v0.26.0 — the plan of record (opened at v0.25.0, 2026-10-08)

Every item carries its **Baseline:**, **Exit:**, **Red-first:** and **Lies:**, and a done item
names its commit, or `tests/test_backlog_items.py` fails. A landed item moves to the State table.
`[ ]` open, `[x]` done, `[~]` handed off. Carried from v0.25.0's Phase 4; ids kept. 4.4 comes
first: 4.2's exit is a wall time on a box that will not be quiet, so it is restated in 4.4's
figure once that is calibrated.

- [ ] 4.2 media_server push, everything changed, warm. Baseline: 170 s at load 4-8, BEFORE the
      incremental coverage build (`06afca4`: 173 -> 76 s on this repo). Measured 2026-10-08
      12:47 under a hold: 208 s, controls 0.30/0.30 s -- the exit is NOT met. Five coverage
      steps are 110 s of the step sum (11-31 s each); clippy, 8 s a crate, comes next. Exit: <= 90 s. Red-first:
      the P0 instrument's per-step sum within 5% of wall on the same run. Lies: a cold sccache; a
      crate the "everything changed" diff did not touch; media_server's own 110 s pytest step
      (a servers item) counted as goh's.
- [ ] 4.3 The P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines. Baseline: 186/147/138
      s (1/2/4, a busy box). Exit: the knee recorded, and `GOH_CI_JOBS`' default set at it.
      Red-first: a run at jobs=1 is no faster than jobs=4, or the steps are serial somewhere.
      Lies: one repo's knee read as every repo's.
      Measured 2026-10-08 under holds. gates_of_heck, every row at ONE pinned commit (`2783f0d`;
      main moved under the unpinned rows): j1 271 s; j2 238 and 236 s; j4 236 s (a noisy 262 s
      re-run clean); j6 noisy twice (233, 260 s). routines at `852b8d5`: j1 174, j2 162 s, j4 161
      and j6 165 s (both just noisy). **The knee is j2 in both repos**: -13% here, -7% there,
      nothing after. Red-first met (j1 is slower than j4). Open: the DEFAULT. `docs/config.md`
      keeps it 1 because only a repo knows which of its steps write one file, so the knee likely
      belongs in each repo's `.gatesrc`, not the global default -- the owner's call.
- [ ] 4.4 A floor stated on a box that is never quiet (O41b, handed over by servers 2026-10-08 at
      the owner's request: "there won't be a quiet box for the foreseeable future"). Baseline:
      media_server on a loaded Mac (load 10-74): `tools/repo_tests.sh` ran ~190 subprocess-heavy
      tests serially, 206-239 s of every push and commit -> 51 s at load 56 with xdist (servers
      `1548344`; 21 s on .33); whole push of `1548344` 190 s at load 33-74; per-crate coverage
      203 s summed over 28 crates (mediaops 50, healthcheck 43, mcp-host 38), musl clippy 54.5 s.
      Exit: a per-step figure that holds within 15% across two loads at least 2x apart (CPU time
      per step, or wall normalised by a same-run control), or a quiet second host (.33, 16 cores,
      load 1-3; no gates_of_heck checkout yet) named as the measuring box; then per-crate coverage
      judged for running across crates at once. Red-first: the chosen figure, taken for one fixed
      step at two loads, moves less than wall time does, or it normalises nothing. Lies: CPU time
      blind to a step that waits (a lock, the network); a .33 figure read as the Mac's. Not
      needed any more: a scoped proven cache for the repo tests -- their cost was serial
      execution, not repetition. Instrument landed: `cpu_ms` (`e3e5418`). First point, 2026-10-08
      20:28, `structural --full` warm over a pinned `e3e5418`, load 110-132: wall 14.2/15.4/16.7
      s, cpu 7.04/7.22/7.34 s (spread 23% vs 4%). The second load (<= 55) is still owed.

## Open — measurements (wall times, taken under holds; 4.4 restates them)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands.

| what | baseline | last | target |
|---|---|---|---|
| this repo's suite, `-n 12 --dist loadgroup` | 94.4 s (v0.20, `-n 8`) | **52.5-53.8 s at load 11-17 (2026-10-06)**, 1987 tests, longest-first (`tests/_schedule.py`); 62-67 s before it on a quiet box | <= 60 s: **MET** |
| media_server push, one crate changed, warm | 197 s | 183 s at load 7-12 (2026-10-06): **110 s is media_server's own pytest suite** (134 tests, run by its gate.sh outside the proven cache); 5 of 29 crates re-gated -- 3 correctly (path users), healthcheck-rs on the whole tree by design (its tests read the repo root), vpn-watchdog-rs never recorded (fixed in `d384c0d`) | <= 30 s: unreachable while the pytest step runs unconditionally; servers `1548344` cut that step to 51 s at load 56 (xdist), not yet re-measured as a push |
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
  After `10be7e8` a reinstall also adds `post-rewrite` and `pre-applypatch` (the conflict-marker
  check for rebase picks and `git am`); `gate.sh --doctor` now names each stock hook a repo lacks.
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
  carries 24 `assert!(..is_empty())` (rust/ 1). `GOH_EXCLUDE='^vendor/'` is set. Not yet told of v0.24.0 or v0.25.0.
- **servers / media_server** [`a1a1d9ac`]: `scripts/dev/check.sh` step 6 runs the pytest suite
  on every push: servers `1548344` (2026-10-08) runs it under xdist, 206-239 s -> 51 s at load
  56, and found its cost was serial execution, not repetition, so a proven step is no longer
  needed for it. `tools/gate.sh:88` still loops crates with `xargs -P 4`: adopt
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
