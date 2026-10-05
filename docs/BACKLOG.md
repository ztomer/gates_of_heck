# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Seeded from EVAL-2026-09-04.md
(delete that file once this list is accepted as the transfer).

## Resume point — 2026-10-05 (opencode session ses_f064f549affe8H5LpvD7S411Y4 stopped mid-work)

Read this section first. This repo had no session of its own after 2026-09-23; its work came from
the app_updates/servers orchestrator (root `ses_f064f549affe8H5LpvD7S411Y4`) and the games
orchestrator (root `ses_f05f17b6dffejZ7dmIqQ9NwIH1`). Both were stopped ~12:20-12:30.

**THE GOAL, AHEAD OF EVERYTHING ELSE: the full suite runs in seconds-to-low-minutes, and pre-push
is fast for the ~30 consuming repos.** Every other repo's session is serialized behind this one,
and no downstream session should edit this repo — a dedicated session owns it and runs first.

**In flight when stopped**

- **"Fix gates_of_heck pytest hang"** (child `ses_ef320d579ffeXWbEkP6tNzLXqQ`, ABORTED after
  ~90 s). Premise: full pytest hangs at
  `tests/test_estate_corpus.py::test_a_declared_entry_verifies_against_the_real_estate[1]`. It got
  as far as measuring: the checker run directly on media_server took 2.3 s (the prompt said 23 s);
  the single test passed in 5.29 s; a 45 s-bounded serial `pytest tests/ -v` was cut off at 13 %,
  inside `test_check_lints_optin.py`, which is slow, not stuck. **Nothing was written in the repo**
  (only `/tmp/ser1.log`, `/tmp/ms_crates_files.txt`). **The premise was stale:** the orchestrator's
  12:20 context summary carried the pre-v0.19.0 state (HEAD `5ec6b13`, 23 s per pass) forward as
  "Blocked: hangs", although its own earlier note had said "not a hang — the checker takes 23s",
  and v0.19.0 (`2ae5340`) had already removed that cost. Treat the hang as RETIRED unless step 1
  below reproduces it.
- **"O32: fix the 20-minute gate suite"** (child `ses_ef41768a9ffekx1J3C90LVBXMr`) COMPLETED and
  shipped v0.19.0: estate sweep scoped to this repo, quadratic in `_spawn_rust.py` removed. Its
  report: 8 workers, wall 233.6 s → 105.6 s, test time 1380 s → 754 s, 1453 passed. It named the
  costs it did not touch: `test_goh_structural_parity` (123 s) and `test_display_seam` (99 s).
- The root's todo list is stale (`check_python_formatted` wiring is done at `5e667c4`; "finish the
  v0.18.0 release" is done — v0.18.0 and v0.19.0 are both tagged and pushed).

**Measured at HEAD `a93dfe1` (v0.19.0) during reconstruction, each command under `timeout 60`**

| measurement | result |
|---|---|
| `pytest '...real_estate[1]' -x -q` | **1 passed in 4.80 s** (5.0 s wall). No hang. |
| `tests/test_estate_corpus.py -n 8 --durations=25` | 13 passed, 5.78 s; slowest `[1]` 5.24 s, `[7]` 1.68 s |
| `test_goh_structural_parity.py` + `test_display_seam.py`, `-n 8` | 66 passed, 32.4 s wall (63 s user, **90 s sys**); `test_both_tiers_run_the_same_steps` 8.7 s; every `test_full_mode_agrees[*]` 5.2-6.4 s |
| consumer `check_no_unreaped_spawn.py`: media_server / routines / ztools | 2.1 s / 0.8 s / 1.2 s |
| consumer `check_probes_pass.py`: same three | 0.1 s each (the estate sweep is stated NOT RUN outside this repo) |

NOT measured here, deliberately: the full suite. Last recorded numbers: 105.6 s wall at `-n 8`
(O32); "1453 passed in 729 s" (games session, 10:38, configuration not recorded, probably serial).
The serial suite is still ~12 minutes, and `tools/pytest.sh` falls back to serial when xdist is
missing. System time above user time points at process spawning, not computation.

**State on disk** (tree clean; `main` == `origin/main` == `a93dfe1`; tag `v0.19.0` pushed)

- `stash@{0}` `wip-orphan-canary-ancestry` (2026-10-03): the games child
  `ses_efbc7d657ffedGWnXOIsTz9bs5`, which reported it redundant with `9f69efb` and left it in
  place. Drop after `git stash show -p` confirms that.
- `stash@{1}` (2026-09-21, a 2-line `Cargo.lock` bump on `9c7db39`): unknown, old, almost certainly
  superseded.
- Worktree `~/.cache/goh/push/MA4NUA`, detached at `bcd444e` (v0.15.1, 2026-10-02), plus 230 `*.out`
  files in `~/.cache/goh/push/` going back to 2026-09-22: **push-gate litter**, an export worktree
  that was never removed. Attribution unknown. It is a cleanup defect in the push gate, not someone's work.
- Worktree `.claude/worktrees/priceless-wu-f3d911` / branch `claude/priceless-wu-f3d911` at
  `362ddd1` (2026-09-27): already merged into `main`, clean, removable.

**Next steps, in order**

1. **Verify v0.19.0's speed claim before building on it.** Run the full suite once, bounded and
   attributed: `timeout 600 python3 lib/bounded_run.py --timeout 590 -- python3 -m pytest tests/
   -q -n 8 --dist loadgroup --durations=40`. Record wall time and summed test time. Expect about
   106 s. If it does not finish, that is a real hang: attribute it with `lib/bounded_run.py` and
   `lib/orphan_canary.py`, don't guess.
2. **Make the suite seconds-to-low-minutes** by measured cost. Start with
   `test_goh_structural_parity` (each full-mode case runs `structural.sh --full` in both tiers over
   a fresh fixture, so build each fixture once and share it) and `test_display_seam`. Then work
   down the `--durations` list. Do not shrink the 55-shape table and do not drop coverage. Re-time
   serial as well.
3. **Measure consumer pre-push end to end** in media_server, routines and ztools, one bounded run
   each with per-step timings. Any shared checker above a few seconds is this repo's to fix.
4. **Hygiene, as class fixes:** make the push gate's export cleanup survive interruption (no
   leaked worktree, no unbounded `*.out` growth) with a test, then prune `MA4NUA`. Drop both
   stashes after a diff check. Remove the merged `priceless-wu` worktree and branch.
5. **The downstream asks below,** then the pre-existing Open items.

**Blocked / owner decisions**

- O33 (servers): "The `gho_` PAT ... **never rotated**", and an older `ghp_` is still live in
  `.90`'s zsh history and three conversation DBs. Only the owner can rotate it.
- `GOH_EXCLUDE` anchoring is still undecided: should a shared key change from a substring match
  to an anchored one (see the ztools row below)?
- Wording for "everyone owns gates_of_heck" and the `:latest` image policy in `~/.claude/CLAUDE.md`
  was a pending orchestrator item. That is the owner's file.

**Cross-repo: what downstream sessions are waiting on**

- **All consumers:** steps 1-3. That is proof that HEAD's suite and pre-push are fast and green.
  The "hang" that the app_updates orchestrator believed was blocking `tools/gate.sh --full` does
  not reproduce.
- **ZoneWM:** `checks/check_file_length.py` skips any path not ending in `SOURCE_SUFFIXES`, so a
  698-line `Makefile` passed the 500-line cap. ZoneWM has split its Makefile and polices it in
  `verify`. The pre-commit line-cap stage still cannot see build files, and that needs a change
  here (`ses_ef35ef237ffeA5dovgl4g80rdq`).
- **ztools:** its `.githooks/pre-commit` and `pre-push` differ from `hooks/` and it has no
  `.githooks/.goh-installed/`, so `install.sh` refuses to reinstall without `--force`. Its agent
  asked for the hooks to converge upstream (`ses_ef35b36e0ffeeLBDhgjRFWzf4Z`). The `GOH_EXCLUDE`
  substring hazard is the other ztools item (Open, below).
  ztools' reconstruction pass (see `ztools/docs/ROADMAP.md` Resume point) adds two
  `coverage_gate.sh` asks that ztools is waiting on before it wires its new gate steps:
  (a) a relative `--floors-json` path silently turns the per-file floor off, so resolve it
  against the repo root or refuse it; (b) an unreadable floors file is currently skipped, and it
  must fail instead.
- **servers (media_server, storage-server, adguard_server):** O33's follow-up is to read
  `.git/config` with `check_no_secrets.py`'s credential-named-key rule. That is already an Open
  item below.
- **games/ZeroThunder:** `garden_drag_flicker.py:73` is ZeroThunder's own fix. Nothing here
  blocks it.

## Open

### From `check_no_unreaped_spawn.py` + `lib/orphan_canary.py` (2026-10-03)

Both landed green across 32 wired repos. These are the residuals, stated rather than left to be
discovered. **Re-measured 2026-10-05 after the v0.18.0 binding fix** — the sweep is the only place
this is true, and two of these changed:

- **A child that calls `setsid()` is invisible to the canary.** It leaves the step's process group,
  and group membership is the only evidence the canary accepts (the `ppid == 1` alternative was
  measured cross-reporting every concurrent gate on the box). A daemonising test helper is a
  different design and would need its own signal — most likely the helper writing its own pidfile.
  **UNCHANGED by v0.18.0**: detection is per-spawn, not per-group, so nothing in that release touched
  this.
- **The checker is per-function, not interprocedural.** A spawn RETURNED to the caller is a handoff:
  counted, never a finding. Four across the estate. Following it means judging a callee's contract,
  which is the caller's judgement. **NARROWED IN ONE RESPECT by v0.18.0, and only that:** a call to a
  function defined in the SAME FILE whose body can panic is now treated as panicking, because
  `routines`' `wait_for_socket` is a local function whose whole body is an `assert!` and it was the
  only construct between that spawn and its reap. A helper in another crate is still an unknown
  contract. A spawn crossing a file boundary remains a handoff.
- **Cross-CRATE guard types are not read.** A guard defined in `tests/common/` *is* (crate scope,
  measured over the estate: two files, both `routines`, and both of them sites the guard FIXES — a
  per-file reading reported correct code as broken). A guard that lives in a different **crate** is
  a dependency's contract and is reported `unjudgeable`: counted and named, never failed on.
- **A kill inside a conditional is accepted.** `if cond { child.kill(); }` satisfies the rule
  statically and does nothing when `cond` is false. Measured cost: unknown; it needs a corpus.
  **UNCHANGED by v0.18.0**, and worth saying plainly: the fix made the rules narrower in SCOPE, not
  stricter in CONTROL FLOW, so this hole is exactly as wide as it was.
- **Three languages.** Rust, Python, shell. Swift, JS/TS and Go are not read. The estate sweep found
  no spawn sites in any `.swift`/`.ts`/`.go` test file, so the cost is currently zero — re-measure
  rather than trust that.
- **A PEP 604 annotation under `checks/` needs `from __future__ import annotations`.** Found by
  running `./tools/gate.sh --full`, not by reading: the self-host install test died with
  `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` because `install.sh` runs on
  whatever interpreter the PATH offers. **A sweep of every module under `checks/`, `lib/`, `gates/`
  and `tests/` found 11 further instances** — one in `checks/` was mine and is fixed; the other ten
  are all in `tests/*.py`, which run under the session interpreter and so are not currently a
  failure. No gate forbids the omission: `pyproject.toml` deliberately enables the ruff FORMATTER
  only, with the comment "enabling a ruleset nobody has run would produce a red tree on day one",
  and enabling one is that repo's separate decision, not this release's.
- **Sweep findings are reported, not fixed** — the index below is **re-measured rather than
  remembered**. One repo is red on a spawn site; the other is an exclusion hazard, not a finding.
  They are in other checkouts, their gates now refuse their own commits, and each repo's own
  roadmap/AGENTS carries its line.

### Estate findings from `checks/check_no_unreaped_spawn.py`

The incident that motivated the checker: `media_server`'s `archive_torznab` test
spawned a server that loops forever and reaped it *after* the assertion that can
panic. **Nine live processes** accumulated, each holding the cargo build lock, and
`cargo test` showed **no output at all** for 30 minutes while the suite itself
finished in 0.22s. The hang was the leak's symptom; the leak was invisible
because the test process was killed before it could report. It cost a day.
`media_server` is fixed (`cc70f6a`, shipped).

The checker landed and immediately found the same shape elsewhere. They are
recorded HERE rather than only in each repo's own backlog, because the class is
shared and the gate that found them is here: a repo whose gate is red refuses its
own commits, and **a red gate nobody has heard of is how a leak becomes an
outage**.

**The table below was RE-MEASURED on 2026-10-05** by running this checker
(v0.18.0, post-fix) in every one of the 32 wired repos, read-only, honouring each
repo's own declared `GOH_EXCLUDE`. The counts are the gate's, not this file's.
The previous version of this table said "four real leaks" over rows that were not
all findings — the `ztools` row was an exemption and the `media_server` row was
already fixed — so the headline number is stated from the rows this time.

| repo | finding | state, measured 2026-10-05 |
|---|---|---|
| **routines** | `tests/follow_lifecycle.rs` — 3 sites: `Command::new("/bin/sleep").arg("300")` with `assert!` between the spawn and the `kill`/`wait` pair. The same shape as the incident, and the same cost: `sleep 300` is a five-minute orphan. | **FIXED** at `7e8cc4c`, shipped in **v0.59.1**. |
| **routines** | `tests/control_lifecycle.rs:227`, `tests/control_takeover.rs:190` — a RAW, unguarded owner beside a `Reap`-wrapped server, reaped below `wait_for_socket`'s `assert!`. A guard on `child` laundered `owner`; **the gate reported both clean.** | **FIXED** in the same `7e8cc4c`, shipped in **v0.59.1**, with all three sites sharing one root cause: the owner fixture and its guard now live once, in `tests/lifecycle_support/`. |
| **monitor** | `crates/multitop/src/ssh/ssh_tests.rs:88` — `child.stdin.take().unwrap().write_all(payload).unwrap()` above `child.wait()`. Two `unwrap()`s that can panic with the child live; the leaked process is the upload script, which starts an `sshd`. | **FIXED**, shipped in **v0.52.0**. |
| **monitor** | the same file's guard, `Reap::spawn`, **with its `Drop` body emptied to nothing**: the gate still reported clean. `Self(` was accepted whenever ANY `impl Drop` existed in the file, so a `Drop` that does nothing passed as protection — precisely the failure monitor's own `a_panic_after_the_spawn_leaves_no_child` test was written to catch, and the gate could not catch it. | **FIXED in v0.18.0** (`_self_type` resolves `Self`; `guard_types` still judges the body). |
| **games/ZeroThunder** | `tests/e2e/live_probe_lib.py:31` — was `return proc if wait_for_app(...) else None`, **dropping the handle on the failure path**, so the failure branch was the leaking one. | **FIXED** at `a6f6962`: `terminate` → `wait(timeout=5)` → `kill`, with the reason inline. (This file previously also carried a "19 files dirty" warning from the R5 retirement; that tree is clean apart from `info.plist`, so the warning is withdrawn.) |
| **ztools** | 6 hits, all under `vendor/camoufox-rs` | **NOT findings** — green via `GOH_EXCLUDE='vendor/'`. Recorded so the 6 are not re-litigated; the exemption itself is measured below. |

**So: one real leak remains unfixed in the estate, in one repo** — and the
count is 1 because it was re-measured, not because the number moved down:

| repo | finding | state, measured 2026-10-05 |
|---|---|---|
| **games/ZeroThunder** | `tests/e2e/garden_drag_flicker.py:73` — `proc = subprocess.Popen([binpath, ...])`, then `if not z.wait_for_app(timeout=15): return 2` at line 76, with `proc.terminate()` at line 119. **The identical dropped-handle defect** as the `live_probe_lib.py` row above, on the LIVE tier. | **UNFIXED, and the gate now says so** (exit 1). Deliberately not fixed: it needs a real screen, and a fix could not be *proven* here, so shipping one would violate prove-before-claim. **NEWLY VISIBLE** — see below. |
| **ztools** | the unanchored `GOH_EXCLUDE` hazard, not a spawn site: `'vendor/'` is a SUBSTRING test compiled with `re.search`, so it exempts any path with `vendor/` anywhere in it. | **UNFIXED**, and still not this gate's to change: re-anchoring `GOH_EXCLUDE` alters the semantics of a key every consumer's checker shares, so it belongs in its own release, in that repo. Harmless today — `vendor/camoufox-rs` is the only match and 106 first-party test files stay policed. |

**Why `garden_drag_flicker.py:73` became visible only now.** It is a Python
site, and Python's panic scan never counted a bare `return` as an exit that skips
a reap — Rust's `PANICS` always did. The asymmetry was not found by reading the
rule; it was found by reading a site the gate could not see. Three further shapes
it had been reporting wrongly were closed at the same time, all measured against
the real file: `return proc` is a HANDOFF and not a skip (that is `live_probe_lib.py`'s
own repaired form, and reading any `return` as a skip reported the repair as the
defect); a `return` under `proc.poll()` is a child already exited; and a `try:`
ABOVE the spawn with the reap in its `finally:` was not seen at all.

The ztools row is the one that could have quietly blinded the gate, so it was
measured rather than believed — planted in a throwaway copy of ztools' tracked
tree, never in ztools itself:

| arm | what it shows |
|---|---|
| real tree, `--exclude 'vendor/'` as `.gatesrc` declares it | green — and **106 of ztools' own test files are still in scope**, so the gate is not switched off over ztools |
| the incident's ORDER planted in `rust/src/units_tests.rs`, exclusion still in force | **RED, exit 1**, naming the panicking line and the reap below it — a genuine spawn elsewhere in ztools is still caught |
| the same violation at `rust/src/myvendor/a_tests.rs` — a path that merely *contains* `vendor/` | **silently exempt, exit 0** |

**The residual, stated rather than left as a feeling:** `GOH_EXCLUDE` is compiled
with `re.search`, not `re.match` — it is a **substring** test, so `'vendor/'`
exempts any path with `vendor/` anywhere in it, not only a top-level vendored
tree. Today that is harmless: `vendor/camoufox-rs` is the only thing it matches in
ztools, and 106 first-party test files stay policed. The house checker's own usage
line says `--exclude '^vendor/'` (anchored, `checks/check_no_unreaped_spawn.py:7`)
and ztools declares the unanchored form. Re-anchor it when ztools' owner is next
in that repo; changing the semantics of a key every consumer's checker shares
belongs in its own release, not in this one. The anchoring is now written down in
`docs/config.md`.

### Remaining from `docs/SUPERSOTA.md` §3

R4, R7 and R8 closed in v0.16.0. R3 **narrowed, not closed** — it cannot judge
its own plant, and it measures house checkers, not the consumer class in R3's
table. Carried here so the residual is not lost:

- **SUPERSOTA R3 — the residual.** `checks/check_estate_corpus.py` plants a
  violation inside a real consumer corpus and requires the checker to go red and
  name it. It proves a checker *still refuses*; it cannot say a checker is
  *correct*, and a plant derived from a real finding needs a per-checker
  language-aware mutator. Consumer-side checks are still unmeasured here.
- **SUPERSOTA R5 — the remaining half.** Nothing refuses a *future* vendored
  copy. Home is `gates/structural.sh`.
- **SUPERSOTA R4a — the honest limit.** The gate-time compare sees a version
  *bump*, not a step added. Closing it needs the binary to embed a hash of its
  source (`build.rs` under `crates/goh/`).
- **Uncommitted gate source is certified, not refused.** A push is certified by
  uncommitted gate source, with a warning. A refusal needs a seam the tests can
  set (an acknowledged-dirty marker file — a `GOH_*` key would need a
  `docs/config.md` row).
- **`tests/test_gate_environment.py` duplicates `_hermetic_env`.** The right
  home is `tests/conftest.py`.

- Secrets v2: entropy heuristics for unknown key shapes. Blocked on FP
  tuning first — v1 (prefixes + key headers) ships instead. Measure FP
  rate on all consumer trees before enforcing.
- shfmt enforcement: dropped from the shell-lint gate (tree uses aligned
  continuations shfmt won't reproduce). Revisit only as reformat-everything
  (noisy) or an explicit shfmt flag set proven clean on this tree.
- Swift coverage: parity-pinned, not merged. A merge needs a real-swift
  oracle proving identical verdicts across Xcode versions; without that
  toolchain, merging violates prove-before-claim. Revisit when it exists.
- Periodic disk watch: `scripts/bin/disk_hygiene.sh --warn-only` wants a
  launchd schedule (the prune-logs plist is the pattern). Manual runs only
  until then.
- `secret-ok` scope: line + line-above only. If fixture pressure appears,
  consider (not promise) path-scoped allow rules — never bare markers.
- Cross-run verdict cache (`GOH_CACHE`): designed, NOT built (Rust-port
  Phase 3d, 2026-09-23). After staged blobs were shared the native scanners
  cost tens of ms; what remains of a staged run is spawns inside delegated
  steps, which a verdict cache does not touch. If it is ever built, every way
  a hit can lie needs a test first: a config key the checker reads but the
  key omits; a blob hashed at another scope than policed (index truth is
  non-negotiable); a cached "clean" over ZERO files (the empty-scope refusal
  must survive a hit); a checker whose version changed. Re-open when a
  measured staged run is dominated by native scan time, not spawns.
- `lcov_merge` native port: killed by its entry gate (Phase 4b). The largest
  consumer (monitor, 6 parts) merges in 76 ms; the bar was a measured >1 s.
  Re-open when a consumer measures a merge over 1 s.
- `check_tests_registered.py` / `check_generated_fresh.py` stay Python: no
  shared gate runs them (Phase 3c scoped to what the gates actually invoke).

### Credential scope left open by `check_no_credential_urls.py` (2026-10-05)

The checker is scoped to git-config URLs and says so in its docstring. Four
adjacent shapes are deliberately NOT covered, each with a reason recorded there
rather than left for a reader to guess:

- **`.netrc` / `_netrc`** — a real credential store, out of scope because it is a
  file outside the repo and no house gate reads from `$HOME` anywhere else. Half
  a checker that reads `$HOME` is worse than none: it works on one machine, and
  the day it is wrong nobody can tell. Re-open when some gate already reads a
  path outside its repo, so there is a precedent to follow rather than invent.
- **`credential.helper`** — measured: multi-valued, and any value may carry a
  token (`!f() { echo username=…; }; f`). It is not a URL, so the rule does not
  reach it. The rule that would: a credential-NAMED key holding an opaque value,
  which is `check_no_secrets.py`'s existing pattern applied to `.git/config`
  instead of to tracked files.
- **`http.*.extraheader`** — measured to carry `AUTHORIZATION: basic <token>`.
  Same shape, same fix, same reason it is separate: it is a header, not a URL.
- **CI configs** — a token in `.github/workflows` IS committed, so
  `check_no_secrets.py` already has it. Duplicating its patterns in a second
  checker would be the second copy a rule about duplicates should never have.
  NOT a gap; listed so nobody re-raises it as one.

The two `.git/config` shapes are the real work and they want ONE change, not
two: read `.git/config` with the credential-named-key rule `check_no_secrets.py`
already owns, so a token in `credential.helper` or `http.*.extraheader` is caught
by the rule that exists rather than by a third variant of it.

## Done (prune to history)

- v0.19.0 (2026-10-05): the R3 estate sweep in `check_probes_pass.py` is scoped to this repo
  (`checks/_estate_sweep.py`). Consumers print "estate corpus sweep NOT RUN" and take 0.1 s,
  measured. The step already runs at full scope only (`gates/structural.sh`). The quadratic in
  `_spawn_rust.py` is gone: a media_server pass went from 23.2 s to 2.1 s.

- Rust-port arc (2026-09-23, `docs/rust-port-plan.md` retired into git
  history): one shared tree walk and a single shellcheck call (staged shell
  lint ~640 ms faster); native home-paths, ceiling + ratchet, no-allow,
  screen presentation, lints opt-in and skills corpus, each parity-pinned and
  red-proven both ways; `goh-golden` bit-identical to the numpy tier
  (numpy's pairwise summation reproduced); staged blobs read once per run
  (197 git spawns → 15 on a 50-file commit).

- v0.8.0 arc: disk watch out of CI, pooled du, parallel suite, shell lint,
  rust coverage floor, secrets v1, swift parity, doctor, timeouts, timing,
  mcp split, xdist grouping, stub dedup, doc refresh.
