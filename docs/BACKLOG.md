# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Seeded from EVAL-2026-09-04.md
(delete that file once this list is accepted as the transfer).

## State — 2026-10-05, v0.20.0 (read first)

The 2026-10-05 resume point (reconstructed after the opencode orchestrators stopped) is worked
through; its text is in git history at `890691f`. **The "pytest hang" is retired**: the full suite
ran to completion at `a93dfe1`, 1453 passed in 109.2 s wall at `-n 8`, no hang.

**Speed, measured on this box** (16 cores, 4-5 of them busy with coreaudiod / FSEvents /
WindowServer at idle; sys time ~= user time throughout, so process SPAWNS are the cost metric):

| what | before | after |
|---|---|---|
| full suite, `-n 8 --dist loadgroup` | 109.2 s, 1453 tests | 94.4 s, 1492+ tests |
| full suite, `-n 12` | -- | 102 s (contention: more workers is slower here) |
| one `structural.sh` run, Python tier, one-file fixture | 3.28 s (1.5 s CPU) | 1.60 s |
| `check_display_seam.py --probe` (run 24x by the suite) | 4.2 s | 1.4 s |
| `check_probes_pass.py` on this repo, probes only | 9.6 s | 3.5 s |
| R3 estate sweep (this repo only) | 9.4 s | 5.2 s |

The causes, each a class fix: `lib/bounded_run.py` polled every 0.2 s, charging EVERY step of EVERY
gate in EVERY consumer up to 200 ms; the display-seam probe made 6 git spawns per fixture where 1
copy does; self-proofs and the estate sweep ran serially. Serial suite: NOT re-timed this round
(the last serial number on record is ~12 min); `tools/pytest.sh` still falls back to serial when
xdist is missing.

**Consumer pre-push, end to end** (`push_gate.sh` fed HEAD's ref line, nothing pushed):

| repo | before (random export path) | after (stable export path) |
|---|---|---|
| media_server | 201 s, green | 242 s first claim (cold), **197 s warm**, green |
| routines | 306 s, red (a script edited mid-run: the parse-guard incident) | 303 s first claim (cold), green |
| ztools | 119 s, red | 113 s, red -- ztools' own code (clippy 1.99 `assert_is_empty`, 72) |

Shared structural layer in each: ~3 s. The rest is each repo's own pipeline; media_server's warm run
spends 1-7 s per crate in the house rust gate across 29 crates.

**Next steps, in order**

1. **Pre-commit is weaker than pre-push for a cheap check.** `python is ruff-formatted` runs at full
   scope only, so two unformatted test files passed pre-commit and refused the v0.20.0 push. The
   stated reason ("a pre-commit hook that reformatted the repository ... would be worse") is about
   REFORMATTING; a `ruff format --check` over the staged blobs of staged `.py` files reformats
   nothing. Add a staged mode to `check_python_formatted.py` (index blobs via `--stdin-filename`),
   wire it in both tiers (`steps_delegated.rs::step_python_formatted`), and re-pin the parity table.
2. Re-time the SERIAL suite once and record it here.
3. The remaining long poles are spawn count, not test count: `test_goh_structural_parity`
   (22 full-mode cases x both tiers) and every test that runs a gate end to end. The lever with the
   widest reach is the native tier running its 7 delegated Python steps concurrently (~0.9 s ->
   ~0.3 s per run, in every consumer's pre-commit) -- measure first, the parity test pins output
   order.
4. The Open items below.

**Downstream: what each consumer session needs to know**

- **Every repo:** re-run `$GOH_DIR/install.sh <repo>`. `structural.sh` now NAMES a hook that is an
  older stock; 14 repos were still on the pre-`0ac0f70` pre-push, which gates the working tree and
  never reaches `push_gate.sh`. Pristine old hooks upgrade without `--force`.
- **Every Rust repo:** `rust_gate.sh` now refuses a missing `Cargo.lock`, runs `cargo metadata
  --locked` first, passes `--locked` everywhere and fails if any step rewrote the lock. A stale lock
  is now RED. `cargo-machete`, `swiftlint` and `shellcheck` absent is now RED, up front.
- **antiknob / divoom:** both gaps from antiknob's PLAN.md are closed upstream (`--locked`, tool
  absence); `tools/lock_guard.sh` is redundant with the gate now and can be retired there.
- **ZoneWM:** `Makefile`, `GNUmakefile`, `makefile`, `CMakeLists.txt`, `[Jj]ustfile`, `*.mk`,
  `*.cmake` are under the line cap in both tiers. `check_probes_pass.py` discovery stays by the
  `check_*` NAME -- deliberately: `input_lock.py --probe` locks the owner's real input, so discovery
  by flag would hand the keyboard to a pre-push hook -- and the gate now NAMES the 23 self-proofs it
  does not run, instead of staying silent.
- **ztools:** its two local hook fixes are upstreamed (`exec bash "$structural"`; pre-push names
  the remedy when `push_gate.sh` is missing). Its hooks still differ textually, so convergence is
  `install.sh --force`, ztools' call. **ztools' HEAD (`40148ae`) is RED** on its own code under
  clippy 1.99: 72 `assert_is_empty` errors (e.g. `src/ztools/model_health_tests.rs:144`).
  **`GOH_EXCLUDE` anchoring: DECIDED, not changed.** It stays a `re.search` substring test: every
  consumer's patterns (`'third_party/|\.generated\.'`) are written for it, and anchoring would
  silently UN-exempt them. ztools should write `'^vendor/'`, as the checker's own usage line does.
- **servers (media_server, storage-server, adguard_server):** `credential.helper` and
  `http.*.extraheader` are now judged (`checks/_credential_config.py`); all three are clean.
- **Finance:** `tests/test_repos.sh:113` and `tests/test_one_plan_of_record.sh:214` set
  `root="$HERE"` and write `"$root.out"` -- a sibling of the repo root. In a push export that was
  the shared export root (230 files accumulated); in the main checkout it is
  `~/Projects/Finance.out`. `push_gate.sh` now removes and NAMES such writes; the fix is Finance's.
- **Newly over the line cap** (build files entered the scope): `CadGoose/CMakeLists.txt` (702),
  `games/CadGoose2/CMakeLists.txt` (1142), `games/necrohand/Makefile` (511).

**Blocked / owner decisions**

- O33 (servers): the `gho_` PAT was never rotated, and an older `ghp_` is still live in `.90`'s zsh
  history and three conversation DBs. Only the owner can rotate it.
- Wording for "everyone owns gates_of_heck" and the `:latest` image policy in `~/.claude/CLAUDE.md`
  is the owner's file.

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
| **ztools** | the unanchored `GOH_EXCLUDE` hazard, not a spawn site: `'vendor/'` is a SUBSTRING test compiled with `re.search`, so it exempts any path with `vendor/` anywhere in it. | **DECIDED 2026-10-05: the key's semantics stay `re.search`** (every consumer's patterns are written for it; anchoring would silently un-exempt them). The fix is ztools' pattern: `'^vendor/'`. Harmless today -- `vendor/camoufox-rs` is the only match. |

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
and ztools declares the unanchored form. The key keeps its `re.search` semantics
(decided 2026-10-05, see the state section above); ztools anchors its own pattern.

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

`credential.helper` and `http.*.extraheader` landed in v0.20.0 (`checks/_credential_config.py`).
Two shapes remain deliberately uncovered:

- **`.netrc` / `_netrc`** — a real credential store, out of scope because it is a
  file outside the repo and no house gate reads from `$HOME` anywhere else. Half
  a checker that reads `$HOME` is worse than none: it works on one machine, and
  the day it is wrong nobody can tell. Re-open when some gate already reads a
  path outside its repo, so there is a precedent to follow rather than invent.
- **CI configs** — a token in `.github/workflows` IS committed, so
  `check_no_secrets.py` already has it. NOT a gap; listed so nobody re-raises it.

## Done (prune to history)

- v0.20.0 (2026-10-05): the per-step 0.2 s poll, the probe's git spawns, serial self-proofs and
  sweep (suite 109 -> 94 s); parse-guarded scripts; tools and `Cargo.lock` as hard inputs; the push
  gate's run ownership, stable export path and reaper (30 GB of orphaned build-dirs, 230 `*.out`,
  one leaked worktree removed); build files under the cap; credential helpers and headers; stale
  hooks self-reporting. Details in CHANGELOG.md.

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
