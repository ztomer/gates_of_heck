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
| C2 (writer half) `hooks/claude/skill_edit.sh`, a PostToolUse hook: the corpus judged when a skill is written | `5780297` | 183 ms per skill edit |
| `tools/session_bench.py`: cross-session serialization, USL-fitted | `22f5bc8` | below |
| spawn cuts: display-seam probe without git per case; `bounded_run` lean (no `ps` when the group is empty, lazy imports, `-S`); `repo_root` asked once; one `git config`; `repo` fixture copied from a template | `1278870`..`a651052` | probe 1.2 -> 0.14 s; per step 59.6 -> 26.4 ms; staged run 16 -> 9 git calls; fixture 41.9 -> 4.8 ms (x614) |
| a build's in-repo reads outside its scope are learned, not fatal (rust proven cache) | `d384c0d` | vpn-watchdog-rs: never recorded -> skipped from the 3rd run |
| P0-P4, C1, C3-C5 | v0.20.0-v0.22.0 | see CHANGELOG |

## Open — measurements (each needs a quiet box)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands.

| what | baseline | last | target |
|---|---|---|---|
| this repo's suite, `-n 8 --dist loadgroup` | 94.4 s (v0.20) | 79-89 s at load 11-17 after the spawn cuts (2026-10-06); workers evenly packed (80-90 s busy each), so wall = summed / 8 | <= 60 s |
| media_server push, one crate changed, warm | 197 s | 183 s at load 7-12 (2026-10-06): **110 s is media_server's own pytest suite** (134 tests, run by its gate.sh outside the proven cache); 5 of 29 crates re-gated -- 3 correctly (path users), healthcheck-rs on the whole tree by design (its tests read the repo root), vpn-watchdog-rs never recorded (fixed in `d384c0d`) | <= 30 s: unreachable from here while the pytest step runs unconditionally -- a servers item (below) |
| media_server push, everything changed, warm | 177 s (P0) | 170 s at load 4-8 (2026-10-06): coverage 269 of ~360 summed step-s (29 crates, each rebuilt instrumented from clean) | <= 90 s: lever is the coverage rebuild |
| any consumer's pre-commit structural layer | ~1 s | media_server, one staged `.rs`: 0.22 s at load 17 (2026-10-06) | <= 0.4 s: **MET** |
| P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines | 186/147/138 s (1/2/4, busy) | provisional | the knee, recorded |

It misses. Levers left by summed time (2026-10-06): `test_rust_gate_scoped_cache.py` ~45 s and
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
