# BACKLOG — the roadmap, and every open item (rule #16: one forward-looking file)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-10, v0.26.0 untagged (read first)

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
| the stdin gate (`goh subprocess-stdin`), ported and landed off by default behind `GOH_SUBPROCESS_STDIN` (1.1, 1.2, 3.1) | `f456f47` | 17 of 34 repos would go red if it were on |
| local_ci's reported log dir holds no FIFO (`grep -r` on it hung) | `2cb3767` | -- |
| release-kit backfills a GitHub release for a hand-made tag | `2573b86` | ztools v3.4.0, v3.4.1, v3.5.0 had none |
| a turn that ends on a large context tells the owner (`hooks/claude/`) | `925fe62` | -- |
| shell lint: a file under `hooks/` is shell only when its name carries no other language; a runner that cannot run ruff or pytest is a named refusal | `3966a90`, `851cc70` | -- |
| P0-P4, C1, C3-C5 | v0.20.0-v0.22.0 | see CHANGELOG |

## Consolidated — the sessions this file carries (2026-10-09, 2026-10-10)

Each row is a fact a later session would otherwise have to re-derive. The 2026-10-09 pass read
ten opencode sessions and one Claude session (2026-10-05..09, all in the `games` estate) and
deleted nine of them (a backup is at `/tmp/opencode.db.bak-220135`). The 2026-10-10 pass read every
opencode, Claude Code and Antigravity session that worked on or reported on this repo since
2026-10-03; once this file landed, the finished ones were moved to the Trash (opencode's exported
to JSON first), and the three Claude sessions still live (routines, Finance, monitor) were left.

| from | what it established, and where it landed |
|---|---|
| games consolidation (`82802c0`) | blocker A: C4 made `GOH_DIR` an export with no `.git`, so `check_plan_refs.py` skipped the whole citation check -- fixed with `GOH_LIVE_ROOT` + `git rev-parse --show-toplevel`, calibrated in two independent directions. Blocker B: `66e638d`'s tracked-ignored gate fired on ZeroThunder; `!_.gitignore` + `git rm --cached` the DMG, gate untouched |
| hook-env sweep x3 | the hook's `GIT_DIR` reaching a throwaway repo, measured as DAMAGE (a real repo's `core.bare=true`, its worktree index rewritten) in two repos; fixed in four consumers by IMPORTING `_gitutil.foreign_repo_env` -- gaf's hand-copied 6-name list deleted, 15 names is git's own answer. Four census copies, four `isdir(.git)` sites, two `run_check(cwd=fixture)` sites -> items 4.1, 4.2 |
| ruff sweep (necrohand) | `gates/py_staged.sh` has no ceiling: 921 findings at HEAD, 69 of them in the files of a finished 1638-line commit, which was refused for lint over debt it did not touch -> item 4.4. Four files were SPLIT, never exempted |
| ZeroThunder fresh-clone build | SwiftLint 0.65.1's baseline key is a string subtraction of `getcwd`; 1034 recorded violations read as new under `/private/tmp`, 0 under `/Users` -> item 4.7 |
| stdin sweep x4 | the class ("a child inherits a stdin it did not ask for"; the shipped incident is a bare `md5sum`) and the port, landed off-by-default as `f456f47`. Estate measured 2026-10-09: 996 findings in 17 of 34 repos -> 3.2-3.4. One brief was WRONG: 123 "remaining sites" were 40 socket clients, 24 seam calls and 17 local wrappers |
| D1 goldens (opencode, games) | forcing the view paused delivers 45 of 90 frames and `golden.py` went 15/15 -> 0/15, so "pin the view paused" is disproved; a consumer-side `GoldenFrameDelivery.swift` (requested vs delivered, `exit(2)` on mismatch) is the prototype for 4.6. Its work is UNCOMMITTED in ZeroThunder (Downstream) |
| Claude `vigorous-noether-9827d8` | the claims gate and its five defects (a probe reusing one estate dir, an absence reading stdout only, derives inheriting `PYTHONPATH` and STDIN -- what hung the metarepo gate 2026-10-05); owner ruling D2' |
| opencode consolidator + its goal turn (2026-10-09 22:00-22:44) | wrote the 2026-10-09 re-phase; then began "implement the roadmap": split the two over-cap files, removed the twice-run steps, took the shared enumeration, added the doc rows -- all uncommitted until `f456f47`. Stopped before re-running the suite |
| Claude, routines (2026-10-10) | `2cb3767` (local_ci's reported log dir held the step FIFO, so `grep -r` on it hung) and `2573b86` (release-kit backfills GitHub releases for hand-made tags). PROCESS BREACHES: `2cb3767` was committed in the main checkout on a red suite, and `2573b86` reached main by `git merge --ff-only` from `/tmp`, past `tools/land.sh` -> item 1.5 |
| Claude, monitor and Finance (2026-10-10) | monitor: an emptied `Reap::Drop` IS caught by `goh unreaped-spawn` (no defect). Finance and ztools: hooks reinstalled (`9443627`, `79698b2`) |
| Antigravity `460d804a` (2026-10-10) | moved the machine to Python 3.15 and skipped this repo ("uses its own 3.13 venv"): this repo's suite runs on 3.13 while every consumer runs its checks on 3.15 -> item 1.6; leftovers in four repos (Downstream). Its `~/.gemini/GEMINI.md` prescribes `[ Ok  ]`-style status helpers, copied into 27 tracked files in 5 repos -> item 4.9 |
| Antigravity `04ebaa74` (2026-09-22) | ZoneWM releases v2.68.0/v2.69.0 committed through the hooks, never `make push`; nothing for this repo |
| ZoneWM session (2026-10-10) | two of its roadmap entries are house tooling and move here: the AST sleep gate (4.10) and the red-run record (4.11); and the GPU lock it found living in ztools (4.12) |

## Resume here (2026-10-10)

- **The tree is judgeable.** The stdin-gate port that sat uncommitted in the main checkout for
  two days landed as `f456f47`, behind `GOH_SUBPROCESS_STDIN` (default off), with the lock at
  0.26.0. Before it landed, `tools/gate.sh --full` over the dirty tree was 5 red of 2239: the
  self-hosting tests reading a 0.26.0 binary against HEAD's 0.25.1 export -- the state, not a
  defect, and it is what 1.4 makes say so.
- **The machine is the owner's to use** (2026-10-10), apart from other sessions doing their own
  work: a measurement may hold the desktop and the GPU (4.12) for as long as it needs, and the box
  is still never quiet, so 5.3's rule (a step's own work, never a quiet wall time) stands.
- **Main moves only through `tools/land.sh`**, and twice on 2026-10-10 it did not (1.5). Every Rust
  change runs `cargo test --workspace` before its commit, and the suite runs before a commit to
  gate or test code.
- **Next, in order:** 1.3-1.6 (the tree tells the truth about itself), then 3.2's `--seed` and
  this repo's own seed, so the stdin gate can be switched on here first.
- Consumers told of a tag: servers and ztools only (the owner's call, 2026-10-08, for quota).
  v0.25.0 was told to no one; v0.26.0 is untagged.
- Owed from 2026-10-08, unchanged: servers' `push-gate-build-dir-config` verified against servers
  (its content is here at `d98d7bd`); 5.3's second calibration point at load <= 55 (method in
  5.3); 5.2 waits on the owner (`GOH_CI_JOBS=2` per repo, or the default left at 1).

## Roadmap to v0.26.0 — the plan of record (re-phased 2026-10-09, rebuilt 2026-10-10)

**What changed in the re-phase.** The v0.25.0 plan of record was three measurement items. The
estate's own work since has found eight defects IN this repo, three of which make a consumer's
`gate.sh --full` red, and one half-finished gate here. So correctness leads and measurement
follows: 1 unblock, 2 stop the registry lying to consumers, 3 land the stdin gate in three
additive steps, 4 close the classes those sessions found, 5 then the measurements (ids kept from
v0.25.0's Phase 4). The 2026-10-10 rebuild keeps that order and adds what the
Claude and Antigravity sessions found: two ways main moved outside `land.sh` (1.5), the house's
a stale venv a Python gate silently prefers (1.6), a status style nothing polices (4.9), and three pieces of
house tooling a consumer had been carrying (4.10-4.12). Every item carries **Baseline:**, **Exit:**, **Red-first:** and **Lies:**, and a done item names
its commit, or `tests/test_backlog_items.py` fails. A landed item moves to the State table.
`[ ]` open, `[x]` done, `[~]` handed off.

### Phase 1 — the tree is judgeable again

- [x] 1.1 (`f456f47`) `Cargo.lock` follows the 0.26.0 bump, so the tree's own `goh` can be built under
      `--locked`. Baseline: `Cargo.toml` 0.26.0 / `Cargo.lock` 0.25.1 across `goh`, `goh-golden`,
      `goh-sys`, `goh-testkit`; `cargo build --locked -p goh --release` exits 101
      ("cannot update the lock file"); `tests/conftest.py:22` sets `GOH_LIVE=1`, which builds the
      working tree's binary with `--locked`, so every pytest invocation aborts with "the working
      tree's goh did not build" (reproduced on `test_config_schema.py`,
      `test_gate_environment.py`, `test_ported_paths.py`). Exit: the suite collects and runs.
      Red-first: `git checkout -- Cargo.lock` puts the collection error back. Lies: a suite that
      runs green against HEAD's `bin/goh` (0.25.1) and reports the port as ABSENT rather than red,
      which is exactly what a stale lock causes.
- [x] 1.2 (`f456f47`) The two structural gates that name the new key and the new forwarder go green. Baseline:
      `tests/test_wiring.py:180` red -- `docs/map.md` has no row for `check_subprocess_stdin.py`;
      `tests/test_gate_environment.py:67` red -- `GOH_SUBPROCESS_STDIN_MIN_CALLS` is in
      `PIPELINE_KEYS` (`crates/goh/src/gatesrc.rs:244`) and in no document. Exit: both green; the
      `docs/map.md` native-check row (`| Check | Source | Purpose | Test |`, `docs/map.md:56`)
      carries the check, its source dir and the tests that pin it. Red-first: delete the row and
      the wiring test reds again. Lies: `test_config_schema_covers_keys` stays green either way
      -- its `SCAN` (`tests/test_config_schema.py:13`) never looks at `crates/`.
- [x] 1.3 (`24e9608`; it also scans `scripts/`, which had three undocumented keys of its own, and skips Rust test fixtures) `tests/test_config_schema.py` scans `crates/`, so a key read only from Rust cannot ship
      undocumented. Baseline: `SCAN = ("gates","checks","lib","tools","hooks","tui","install.sh")`
      (`:13`); `GOH_SUBPROCESS_STDIN_MIN_CALLS` is read at `crates/goh/src/gatesrc.rs:212` and
      listed at `:244`, appears in no doc, and that test passes -- while `AGENTS.md:62` claims
      "Adding a key without documenting it fails `test_config_schema_covers_keys`" without
      qualification. Exit: removing the key's `docs/config.md` row reds the test. Red-first: add a
      throwaway `GOH_` read under `crates/` and watch it go red. Lies: a Rust key that a shell
      script also reads is already covered; only the Rust-only case is the hole.
- [x] 1.4 (`c02ec73`: `crates/goh/structural_steps.txt`, embedded and listed by `goh structural --list-steps`; named as a warning, since a step-adding commit is judged by HEAD's binary by design) A structural step that exists in the tree but not in the running binary is NAMED, not
      silently absent. Baseline: `gates/structural.sh:15` runs HEAD's export (`_from_head.sh`),
      `bin/goh` is 0.25.1 against a 0.26.0 tree, `goh --help` has no `subprocess-stdin`, and
      `tools/gate.sh --staged` printed `all structural gates passed` in 0.3 s with the new step
      never having run. `goh_require_current` (`structural.sh:132`) compares versions, which both
      sides satisfy at 0.25.1; `_from_head.sh:86-93` warns only for a LINKED worktree with an
      uncommitted edit to the ENTRY SCRIPT, so the shared checkout stays silent by design.
      Exit: a staged run names every step declared in `crates/goh/src/` that the running binary
      lacks. Red-first: drop the naming line and the silence returns. Lies: the source-stamp
      comparison in `gates/_goh_bin.sh:62-70` passes here because both sides are HEAD; a stamp is
      not a step inventory.

- [x] 1.5 (`7e26571`, `GOH_LANDED_ONLY=main` in this repo's `.gatesrc`) Main moves only through `tools/land.sh`, and a push says so. Baseline: on 2026-10-10
      `2cb3767` was committed in the main checkout while `pytest tests/ -x` was red, and
      `2573b86` reached main by `git merge --ff-only` from a `/tmp` branch, both past `land.sh`,
      whose rule lives only in this file and in its own header. Nothing records which tips it
      gated. Exit: `land.sh` records the SHA it gated and merged; the pre-push hook of this repo
      refuses to push a `main` whose tip it never recorded, and names the commit and how to land
      it. Red-first: `git merge --ff-only` a branch into main and push -- refused. Lies: a record
      kept in the worktree is lost with it; keep it in the main checkout's git dir, and a record
      matched on a branch name rather than a SHA passes a moved branch.
- [x] 1.6 (`61326f3`; `gates/_py.sh` `goh_py_venv_current`, `tests/test_py_venv_currency.py`) A stale project venv cannot silently become the interpreter a Python gate judges with.
      Baseline: `gates/_py.sh` prefers `.venv/bin/python` over `python3`, unconditionally. Read
      2026-10-10: this repo's own suite never used its `.venv` (`tools/pytest.sh` runs `python3`,
      3.15.0), and the `.venv` was an EMPTY uv venv on 3.13.11 from 2026-10-05 (moved to the
      Trash) -- so the 2026-10-10 survey's "tested on 3.13, run on 3.15" was wrong here. The
      class is real for consumers: a repo whose `.venv` lags `python3` has its ruff and pytest
      steps judged on the older interpreter with no word said. Exit: `_py.sh` names the
      interpreter and version it chose on the step line, and refuses a `.venv` whose version is
      below `python3`'s unless the repo declares the pin (`GOH_PY_RUNNER`, or a
      `.python-version` it commits). Red-first: a fixture repo with a 3.13 venv and a 3.15
      `python3` reds; with the pin declared it passes. Lies: a venv built from the same minor but
      missing the tools is a different failure (already a named refusal, `851cc70`).
### Phase 2 — the calibration registry stops lying to consumers

- [x] 2.1 (`752ff01`, and game_asset_factory `95907e3`, which now calls this reader instead of a copy) ONE rule for "this test proves this gate", settled here and published to the estate.
      Baseline: `~/Projects/games/game_asset_factory/tools/check_gate_calibration.py --report`
      prints 8 citation drifts against this repo -- `claim_derivation`, `lock_version`, `md_links`,
      `no_credential_urls`, `no_empty_assert`, `no_unreaped_spawn`, `tag_version`,
      `version_provenance` -- while this repo's own reader, `checks/check_probes_pass.py
      --no-corpus`, exits 0. Measured cause: the estate probe runs `<cited> --proves`; a pytest
      module ignores an unknown flag, exits 0 with empty stdout, and an empty set reads as "proves
      nothing". All 8 citations resolve to real committed `tests/*.py` files. This is a defect in
      the CONSUMER'S probe, not eight drifts here -- and rule #8 applies: it is fixed, not filed.
      Exit: the estate reports this repo the way this repo reports itself, and a citation to a
      prover that cannot answer is a finding that says so. Red-first: point the probe at a module
      with no `--probes` and the drift returns. Lies: silencing the estate tool instead -- two of
      its own repos read their coverage from it.
- [x] 2.2 (`752ff01`: three entries cite the tests that drive each gate red; the estate reads 1 unproven, its ceiling; game_asset_factory `5f0985e` canaries two more) The unproven-gate ratchet is back under its ceiling. Baseline: 4 unproven against a
      ceiling of 1 -- `check_dep_currency`, `check_python_formatted`, `check_subprocess_stdin`,
      `check_swift_coverage`; only `check_swift_coverage` carries a reason in
      `checks/gate_calibration.json`. `check_subprocess_stdin` is in the list because its forwarder
      is untracked, i.e. it is counted before it exists. Exit: at or under the ceiling, or each
      entry carries a why the registry names. Red-first: raise the ceiling, or delete a why, and
      the gate reds. Lies: an entry can leave the list by being untracked rather than proven.

### Phase 3 -- the stdin gate, in three additive steps

The ruling from the estate's sessions stands and is kept: **hooks run whatever is in this
checkout, so the gate lands last of the gate work -- every repo gets its fix or its ratchet
first.** What changed is the shape of "first": the ratchet becomes something a repo GENERATES
with one command (3.2) instead of something a session hand-writes per repo, because the ruff
episode (4.4) is what that hand-writing costs.

- [x] 3.1 (`f456f47`, off by default behind `GOH_SUBPROCESS_STDIN`) Finish the port in this repo without turning the estate red. Baseline: with the tree,
      9 of 2213 pytest tests fail. Two files are over the cap (`substdin/mod.rs` 588,
      `calls.rs` 525, against `GOH_MAX_LINES=500` -- the house rule is split, never exempt);
      `claims` and `vendored` each execute TWICE per run (`structural.rs:113-118` and again in
      `late_steps()`), which is what `test_every_announced_step_is_timed_once` reds on;
      `substdin::step` re-enumerates the tree with `gitutil::listed_files` where the pipeline
      already built `files` (`structural.rs:40`), costing 9 -> 11 git spawns per `--staged` run
      (`test_goh_git_spawns`); `mod.rs:481` hardcodes the allowlist filenames instead of
      `ALLOW_FILE`/`ALLOW_DIR`; `vendored.rs::RETIRED` and
      `tests/test_goh_structural.py::INVENTORY` (`:139`) are unupdated;
      `tests/test_exclude_scope_doc.py` CONSUMERS lacks the module. Exit: the named tests green,
      the two files split, the step wired but not yet always-on. Red-first: each named test is
      already the red. Lies: `cargo test` green is not the suite; only the suite holds these pins.
- [x] 3.2 (`498eb9a`; `substdin/seed.rs`, `tests/test_subprocess_stdin_seed.py`, the floor's break-prove in `tests/test_subprocess_stdin_scope.py`, which found `5680a8d`) The migration path: a `--seed`, and the test tiers the port has none of. Baseline: 25
      inline Rust unit tests, ZERO pytest, zero break-probe, and no way to write an allowlist --
      `substdin/allow.rs:5` claims it "was seeded on 2026-10-08 with every violation the estate
      had that day -- 511 in this repo alone", and no `subprocess_stdin_allow.json` or
      `subprocess_stdin_allow.d/` exists in ANY of the 105 git trees under `~/Projects`.
      `test_install.py::test_self_hosted_repo_satisfies_its_own_hook` is red for exactly that.
      Exit: `goh subprocess-stdin --seed` writes today's findings as entries (a reason optional,
      its absence counted as `unreviewed` debt on the passing line); a pytest tier and a CLI tier
      exist beside the unit table; a break-prove goes red when the scanner stops seeing calls.
      Red-first: point it at a fixture whose Python is fully excluded and it returns the blind-
      scanner verdict; revert one declaration and the findings return. Lies: a gate that seeds
      itself is a gate that cannot fail -- so seeded entries are `unreviewed` and counted until a
      human re-earns them, and a seed run must not also edit code (two changes, one commit).
- [ ] 3.3 The estate sweep. Baseline: MEASURED 2026-10-09 with this tree's own binary, scope
      `git ls-files --cached --others --exclude-standard`: 34 repos carry Python, 1361 process
      calls, **996 findings**, 17 repos exit 1. gates_of_heck 511 (51.3% of the estate, in the repo
      that owns the check), ZoneWM 118, media_server 88, Finance/zinc 71, divoom-control 52,
      scripts 52, monitor 39, koffee_big 23, ztools 13, routines 8, games 6, app_updates 5,
      old_phones 4, Finance/salary 2, Finance/zinc-core 2, CadGoose 1, sys_updater 1. Already
      swept and clean: ZeroThunder 165 calls, necrohand 76, CadGoose2 23, game_asset_factory 41.
      10 estate repos track no Python and exit 0 "not applicable". The debt is SPREAD: 389 files
      carry it and the largest single file is 16 (`tests/test_install.py`), so seeding is a
      per-file exercise, not a few hot spots. Exit: every repo in that table either declares its
      stdin or carries a seeded allowlist at or under today's count. Red-first: run the check in
      a repo with no allowlist -- measured today, exit 1 in all 17. Lies: an exclusion can hide
      the subject; `GOH_EXCLUDE` is the only key this check reads (only media_server's removes
      Python: 2 vendored files, 0 findings) and `GOH_LINE_EXCLUDE` cannot affect it at all.
- [ ] 3.4 The flip: `GOH_SUBPROCESS_STDIN` defaults to `on`. Baseline: `GOH_SUBPROCESS_STDIN_MIN_CALLS` is set
      in 0 of 34 repos, and 18 have a keyless `.gatesrc` so they cannot express a floor at all;
      ZeroThunder still carries its own `tools/check_subprocess_stdin.py`, which the vendored-copy
      gate already names as a copy to delete. Exit: the step is in the always-on group at both
      scopes, this repo declares its floor in `.gatesrc`, and an undeclared call is red at its
      author's next commit in any repo. Red-first: a repo whose Python is excluded away entirely
      must still say it read none. Lies: flipping before 3.3 makes 17 repos' next commit red for
      debt nobody has looked at -- the order is the earlier ruling's, and today's measurement is
      why it is a phase and not a note.

### Phase 4 -- the classes the 2026-10-08/09 sessions found

- [x] 4.1 (`92b0946`: `_gitutil.is_work_tree` and `checks/_gitcensus.py`; the consumers' half is in Downstream) One answer to "is this a repository", and one census for where a git argv may be built.
      Baseline: `checks/_gitutil.py` exports `local_env_vars`, `foreign_repo_env`, `scratch_git`
      and NO `is_work_tree`; the shape `os.path.isdir(root/.git)` was found in FOUR places across
      two repos (one named in the brief, three found by grepping the shape), and it is False in
      every linked worktree, where `.git` is a file -- so a gate asking it inspects nothing and
      passes. The Python twin of `tests/test_rust_test_code_spawns_git_only_through_the_testkit`
      now exists as FOUR consumer copies. Exit: `_gitutil.is_work_tree()` (git's own answer,
      `realpath` on both sides) plus a house census the consumers import instead of
      re-implementing; the four copies delegate or go. Red-first: `is_work_tree` back to
      `isdir(.git)` and the linked-worktree case reds; plant a hand-rolled git argv and the
      census names the file. Lies: a census that matches ARGV rather than the CALLEE flags the
      very seam it exists to approve -- a gate that can never go green reports nothing (measured,
      CadGoose2).
- [x] 4.2 (`dde34ef`: `_gitcensus.directory_spawns`, `Census.fixture_sites`/`stale_inherit`, this tree censused by `tests/test_goh_directory_spawns.py`; the consumers' half is in Downstream) A child spawned INSIDE a fixture repo inherits the hook's repository, and no grep can
      see it. Baseline: two live instances found in one day -- gaf's
      `tools/check_gate_canary.py::run_check` and ZeroThunder's `run_in`/`emoji_rejected` -- each
      launching a HOUSE CHECK with `cwd=<fixture>`, so the argv names a checker rather than `git`
      and every AST census is blind to it. The failure is silent and INVERTED: the canary reports
      that a checker "PASSED a known violation" when the checker was policing the real tree.
      Exit: the census reaches the shape -- any spawn whose cwd is a scratch repo carries the
      scrubbed env -- with both known instances named in its tests. Red-first: remove the scrub
      from `run_check` and it reds naming the fixture. Lies: two is a LOWER bound; the shape is
      "any process whose cwd is a fixture" and only the two named were ever looked for.
- [ ] 4.3 Every scanner declares its subject, so a scan that stopped reading says so. Baseline:
      the floor key is set in 0 of 34 repos, and 18 repos cannot express one. The blind-scanner
      class was found independently in four repos: an `__all__` omitting a name, a gate regex
      dropping gates declared with a third tuple element, and a regex `ruff format` broke, which
      silently vanished THREE gates from a calibration. Exit: one key per scanner in the house
      shape already in `substdin` (unset = no floor, unparseable = exit 2 naming the key), every
      always-on scanner carries one, and a keyless `.gatesrc` says so. Red-first: exclude every
      `.py` in a repo that has one -- the "read NONE of them" verdict, measured today on a
      fixture. Lies: a floor set too high reds a clean repo and gets raised; too low never fires.
      The exit is the PAIRING with the stale-allowlist check, in both directions.
- [x] 4.4 (`61b4a91`; `gates/py_lint_ceiling.py`, opted into with `GOH_PY_LINT_CEILINGS`, seeded by `--seed`; `ruff format --check` stays strict) `gates/py_staged.sh` grows a ceiling, so lint debt is visible and shrinkable instead of
      discovered the day someone edits the wrong file. Baseline: `py_staged.sh:46-47` runs
      `ruff check` and `ruff format --check` on staged `.py` with no baseline and no opt-out; the
      only key it honours is `GOH_PY_RUNNER`. necrohand measured 921 findings across 96 files at
      HEAD (ruff 0.16.10, the version its own `requirements.txt` pins -- real debt, not drift),
      69 of them in the files of a FINISHED, fully-tested 1638-line commit, which the hook
      refused for lint over debt it did not touch; clearing it took 96 files (+5414/-3350) and
      two codemods proven by differential execution over 326 cases. A repo with no `ruff.toml`
      therefore cannot satisfy its own commit gate at all. Exit: a per-repo ceiling in the shape
      `check_baseline_ratchet` already has -- it may FALL, a raised ceiling is a finding, and
      deleting the ceiling file is a finding. Red-first: add one UP031 and it reds naming the
      file; raise the ceiling and it reds. Lies: deleting the file resets the debt, and a stale-
      high ceiling proves nothing -- both are findings, which is the point.
- [x] 4.5 (`b0571d7`: `gates/py_noqa_parity.py`, opted into with `GOH_PY_NOQA_PARITY=pyflakes`; staged at commit, every tracked file at push) One suppression vocabulary per linter. Baseline: `# noqa: F401` silenced ruff but NOT
      necrohand's own pyflakes gate, which reported 4 unused imports in a file the sweep had just
      cleaned; the same repo's 134 stale `noqa`s were each read before removal and 9 survive. The
      house forbids `#[allow]`/`#[expect]` in Rust (`checks/check_no_allow.py`) and has no Python
      equivalent. Exit: where a repo runs two Python linters, a parity gate over suppression
      directives; a `# noqa` one linter honours and another ignores is a finding. Red-first: put
      a `# noqa: F401` over a live unused import. Lies: RUF100 proves a directive is unused, not
      that a surviving one is still RIGHT -- those are different claims.
- [ ] 4.6 The frames-drawn invariant: a harness that requests N frames reads back N.
      Baseline: ZeroThunder, measured 2026-10-08 -- 14+ harnesses pass `--golden-frames 90`
      (`tests/e2e/golden.py:206`, `golden_bistability_core.py:268`, and 12 more) and NOTHING
      compares frames drawn to frames requested. The golden loop drives its frames through the
      app's ON-SCREEN `MTKView` (`AppDelegate+GoldenReport.swift:238`, left display-linked at
      `AppDelegate+Setup.swift:199`), so with the built-in panel on the view drops every other
      draw -- 45 frames of 2 ticks -- and the resulting arm tracked the WindowServer display log
      at 7 of 7 observations. `golden_bistability.py` exists and is blind to it BY ITS OWN
      DOCSTRING: it catches a latch, and a display-paced render is a systematic half-rate,
      deterministic within a display state. Exit: `crates/goh/src/screen.rs` (tests touching the
      screen) and `checks/check_display_seam.py` (app source presenting outside a declared seam)
      grow a third rule -- an offscreen render paced by an on-screen view -- and a harness that
      requests N frames fails unless it reads N back. Red-first: a fixture rendering at half rate
      must red on the COUNT, not on a pixel. Lies: a harness that reads back a constant is not
      measuring; the count must come from the renderer that drew. Consumer-side, named there:
      `ZeroThunder/docs/ROADMAP.md:523` still carries a `CLOSED 2026-10-01` whose stated premise
      was measured wrong, and the metarepo's claims registry holds a `plan.golden-pass` truth that
      is a function of the HOST's display state -- it went DRIFTED the moment the lid closed.
- [ ] 4.7 A gate's verdict must not depend on where the tree is. Baseline: ZeroThunder,
      2026-10-08 -- SwiftLint 0.65.1's baseline key is `relativeDisplayPath`, a string subtraction
      of `getcwd`, and its two file-walk branches produce absolute paths that disagree under any
      firmlinked path -- 1034 recorded violations all reported as new from `/private/tmp`, 0 from
      `/Users`. `--no-cache`, an absolute baseline path, re-recording the baseline in place and
      symlinks were each measured and EXCLUDED as the trigger; the trigger was a provably no-op
      `excluded:` block (both configs lint 323 files with the same 1034). Exit: a check that runs
      a structural gate in a scratch copy under a firmlink and compares the verdict. Red-first:
      the SwiftLint-shaped case in a fixture must go red before the check exists. Lies: the
      trigger is a firmlinked path, not `/tmp` (a worktree at `/Users/ztomer/zt-wt-locate`
      passes), so a check that tries one prefix misses the class.
- [x] 4.8 (`bc5c712`: `crate::callkey`, for kill-by-name and subprocess-stdin; the claims sibling is 4.13) The kill-by-name ratchet keys on a LINE, so a reformat reads as a policy violation.
      Baseline: `crates/goh/src/killname/mod.rs:297` keys an entry on whitespace-stripped source
      text, matched at `:316`; the stale diagnostic at `:352` gives ONE message for both causes.
      Measured: necrohand's `qa_minigames.py` needed three re-keys in one afternoon as the ruff
      and stdin sweeps reflowed the call, and the gate reported `["pkill","-f","MacOS/Necrohand"]`
      from line 274 -- it keys on the argv TEXT, not the whole call. Exit: the key is the callee
      plus its normalized arguments, so a reflow cannot revoke a judgement while a deleted call
      still does, and the diagnostic names which of the two it found. Red-first: revert the new
      key and a reformatted-but-live kill goes red again; delete the call and it stays red.
      Lies: normalizing arguments is a parse, and a parse guesses about intent -- a miss must
      SAY so rather than pass.

- [x] 4.9 (`51109d6`, as `goh status-markers`, a command a repo's gate calls like 4.10; this repo runs it in GOH_CI_STEPS; GEMINI.md now points at the house libs; ZoneWM reads 0 and four repos adopt it in Downstream) Hand-rolled status markers are a finding, as emoji are. Baseline: the house style is
      `tui/lib.sh`/`tui.lib` (`info/ok/err/warn`), and nothing polices the other direction:
      `~/.gemini/GEMINI.md` tells Antigravity to write `print_info`/`print_err` helpers printing
      `[ ==> ]`, `[ Wrn ]`, `[ Err ]`, `[ Ok  ]`, and 27 tracked files in 5 repos carry them
      (ZoneWM 6, e.g. `tools/analyze_border_rhythm.py:27-37`; antiknob 10; divoom-control 9;
      routines 1; ztools 1). Exit: a structural step refusing a bracketed status marker and a
      hand-rolled `\033[` colour in source, seeded per repo (shrink-only), and GEMINI.md pointing
      at the house libs instead of defining its own. Red-first: plant `print("[ Ok  ] done")`.
      Lies: a marker inside a test fixture or a doc that quotes the forbidden style is not a use
      -- the scope is source, and the seed names each file.
- [x] 4.10 (`8e00c6e`, as `goh fixed-sleep`, a command a repo's gate calls, not a structural step) A fixed sleep never stands in for a readiness signal (from ZoneWM, which owns its seed).
      Baseline: ZoneWM's `tools/` holds 155 `time.sleep(` calls across 51 files, and three times a
      sleep stood in for a signal (a capture racing its work, a harness reading a later marker on
      an earlier signal, a lock read as held after "not exited after 1 s"); its
      `check_test_sleeps.py` covers Swift tests only. Exit: `goh` rule, from the AST, refusing a
      `time.sleep` whose next read is a spawned process's `poll()`/`returncode` or a marker file,
      unless the sleep sits in a loop bounded by a deadline that refuses on expiry; a per-repo
      seed, shrink-only. Red-first: each of the three historical shapes planted red, the bounded
      loop clean. Lies: a sleep before a read through a helper the AST does not follow is
      invisible; the seed lists it with its reason rather than passing it silently.
- [x] 4.11 (`e526df9`: `goh red-run`, opt-in by `GOH_RED_RUN`; no repo has it on yet, the owner's call per repo; presence and substance, truth a review rule) A new test's red run is recorded. Baseline: "prove a new test can fail" is written in
      this repo's rules, ZoneWM's, and the global ones, and enforced nowhere; the pass cache
      records passes only. Exit: a commit that adds a test carries a `Red-run:` trailer naming the
      mutation that turned it red, checked by the stock commit-msg hook beside `goh commit-class`
      (opt-in per repo, `GOH_RED_RUN=1`). Red-first: a commit adding a test without the trailer is
      refused; with it, accepted. Lies: a trailer is checked for presence, not truth -- the exit
      also records the red run's output digest where the repo's mutation runner writes one, and
      where it cannot, the rule is a review rule and says so.
- [x] 4.12 (`3103441`; the consumers' half is in Downstream) The machine's locks live in one place: the GPU joins the desktop. Baseline: the GPU lock
      is ztools-only (`tools/gpu_lock.sh`, `rust/src/ztools/eval/gpu_lock.rs`,
      `/tmp/mac-osaurus-gpu.lock`, `mkdir`-atomic, owner pid + start time, a 4 h no-progress
      ceiling), and its header cites `~/projects/scripts/lib/desktop_lock.sh`, a path gone since
      the desktop lock moved here. `tools/quiet.sh` holds the desktop and every gate but not the
      GPU, so an osaurus run can sit inside a measurement hold, and `quiet.sh:12` says ZoneWM's
      probes respect the desktop lock when only its `border-clip` and `qa-sweep` take it. And
      `~/Projects/scripts/lib/test_desktop_lock.sh` (15110 bytes) is a second, drifted test of
      this repo's `lib/desktop_lock/` (whose own is 6472). Exit: `lib/gpu_lock/` (sh + py, one
      on-disk format pinned by a shared fixture the ztools Rust client also reads), ztools
      sourcing it, `quiet.sh` holding it, its claim about ZoneWM true, and one desktop-lock test.
      Red-first: a held GPU lock makes `quiet.sh` refuse. Lies: two implementations of one lock
      file drift silently; the fixture is the only thing that holds them together.

- [x] 4.13 (`451d711`: `Claim.span` is the key, and the allowlist is no longer read as prose) The claims allowlist keys on a markdown LINE, so re-wrapping a paragraph revokes an
      exemption (the sibling 4.8 found). Baseline: `crates/goh/src/claims/mod.rs:210` compares an
      entry's `claim` with the whole line the claim sits on (`text::Claim.text` is the line,
      `claims/text.rs:155`), whitespace collapsed; a re-wrap moves words onto or off that line.
      Exit: the key is the claim's own span (number, unit, target), and an entry holding the old
      line still matches the claim inside it and is named for re-keying, as `crate::callkey` does
      for calls. Red-first: re-wrap an allowlisted claim's paragraph and it stays excused; change
      the claim's number and the entry is stale. Lies: two claims on one line share the old key;
      the span key separates them, so a seed written before it must be read as fragments.
- [x] 4.14 (`a74bbff`: `crates/goh-tui`, goh prints through it; red-first run: `ICON_OK="+"` printed `+ [status_markers] ...` with no code edit; the consumers' half is in Downstream) A Rust binary has no house TUI, so its status lines are hand-rolled by necessity (4.9
      found it). Baseline: `tui/lib.sh` and `tui/lib.py` read `tui/stylerc`; a Rust binary has
      nothing to read it with, so goh's own output and antiknob's 113 `[ Ok  ]`-style sites (9
      files, mostly `src/bin/`) each spell their own glyphs and colours, and `goh status-markers`
      can only point them at stylerc's glyphs. Exit: a `tui` crate in this repo that parses
      `tui/stylerc` at build time and exposes `info/ok/err/warn`, `NO_COLOR` and non-tty aware;
      goh prints through it. Red-first: a stylerc glyph changed shows in goh's output without a
      code edit. Lies: a crate a consumer pins by git rev is a vendored copy with extra steps --
      the crate must be consumed from `$GOH_DIR`, as the libs are.

### Phase 6 -- the estate consolidation's findings (2026-10-12), ahead of Phase 5

Reported by the 2026-10-12 roadmap-consolidation session, whose per-repo agents measured them
(6.10-6.13 from its last agents: games meta, koffee, monitor);
the paths and SHAs are theirs and each Baseline is to be re-read before its round. Correctness
outranks measurement, so this phase runs before Phase 5. Two of its findings were already here:
the Rust TUI helper is 4.14 (antiknob's 43 `[ ==> ]` sites are its consumer half, in Downstream)
and the calibration registry is O35.

- [x] 6.1 (`85d0b51`: `checks/check_git_env.py` runs `_gitcensus.census` over git's listing,
      configured by `<gate dir>/git_env.json`, and refuses a census that read no Python, a
      declaration with no reason and one that matches nothing; the scrub was already git's own
      list and is now pinned for GIT_PREFIX; the consumers' half is in Downstream)
      The git-env seams live here once, and no consumer carries a copy. Baseline: necrohand
      `tools/check_git_env.py` (`e46b8f9`) and CadGoose2 `tools/check_git_env.py` (`f0b4bb9`)
      are hand-written copies of the empty-scope refusal, and both now fail structural with
      "PASSED over an empty tree", so both pushes are refused; necrohand also carries a stale
      excuse ("check_mcp_server.py is excused here but now FAILS on an empty tree"). ZeroThunder's
      pre-push fails `tests/test_hook_git_env.py::test_the_seam_scrubs_foreign_roots_and_keeps_this_repo`
      with `AssertionError: this suite is running under a hook's variables ['GIT_PREFIX']; nothing
      below bites` -- its copy of the hook-env scrub does not know `GIT_PREFIX`. Exit: the
      empty-scope refusal beside `_gitutil.foreign_repo_env()`, which itself scrubs every variable
      git sets in a hook (`GIT_PREFIX` included, the list read from git's own docs, not typed), and
      the three copies delegate or go. Red-first: a hook environment carrying `GIT_PREFIX` makes
      the seam's test red until the scrub names it; an empty scope handed to the house refusal
      reds. Lies: a scrub that lists variables by hand misses the next one git adds -- the test
      reads git's set, so a new one reds instead of passing.
- [x] 6.2 (`1c4be61`: `gates/python.sh` runs the repo's pin -- a `.venv`, else `GOH_PYTHON_VERSION` or a
      committed `.python-version`, found by version and read back -- with `$GOH_DIR` on its path and
      the shell's `PYTHONPATH` dropped; `gates/_py.sh`'s runner takes the same pin; the structural
      step "no bare python in a hook or gate" (`crates/goh/src/barepy`) refuses the other way. goh's
      own stdlib-only helpers in `gates/*.sh` still call `python3`, most with `-S`: no import of
      theirs depends on the interpreter. The consumers' half is in Downstream) A gate runs a pinned interpreter with its own import path, never the ambient `python3`.
      Baseline: Homebrew moved `python3` to 3.15.0 on 2026-10-10 09:27; since then necrohand fails
      `ModuleNotFoundError: spacy` and check-hemingway reports INCOMPARABLE, scripts fails 2 tests
      because coverage's startup hook moved, and ZeroThunder's commit hook fails `No module named
      'tui'` unless `PYTHONPATH` is set. 1.6 names the interpreter a step chose and refuses a stale
      `.venv`; it does not stop a bare `python3` resolving to whatever Homebrew installed last.
      Exit: one seam (`gates/_py.sh`) every gate's Python goes through: the interpreter pinned per
      repo, `$GOH_DIR` on its path without the shell's `PYTHONPATH`, and a structural step refusing
      a bare `python3` in a hook or gate script. Red-first: a hook that runs `python3 -c "import
      tui"` with `PYTHONPATH` unset reds today and is green through the seam. Lies: pinning a path
      to a Homebrew keg pins nothing (the keg moves) -- the pin is a version, checked.
- [x] 6.3 (`4686280`: `crates/goh/src/urlexpand.rs`, read by `goh secrets`; the consumer's half is in
      Downstream) The credential-in-URL gate sees a variable in the userinfo. Baseline: it passed
      `git clone "https://${TAP_TOKEN}@github.com/..."` at CadGoose
      `.github/workflows/build_and_release.yml:154`. Exit: userinfo made of a shell or Actions
      expansion (`${X}`, `$X`, `${{ secrets.X }}`) is a finding, in YAML as in shell. Red-first:
      that line planted in a fixture reds. Lies: an expansion is not a literal secret, but it is
      the secret in the URL at run time, logged by every tool that echoes its argv.
- [x] 6.4 (`20281bb`: `crates/goh/src/rustlex.rs`, one lexer with its state carried across
      lines -- strings, raw strings at any hash count, character literals told from lifetimes,
      nestable comments; `goh empty-assert` reads through it too)
      `goh no-allow` reads attributes, not text. Baseline: it flags `#[allow]` inside a string
      literal at zinc `crates/xtask/src/walk_tests.rs:28`. Exit: a match inside a string or a
      comment is not an attribute. Red-first: that line in a fixture is clean; a real
      `#[allow(dead_code)]` beside it is still red. Lies: a raw string holding a whole attribute
      line is the case a lexer gets wrong -- the fixture carries one.
- [x] 6.5 (`5c5868e`: `release.sh` step 0 reads `gh run list --commit HEAD` and refuses a red,
      pending or missing run, naming each; no tracked workflow says "no CI", never green;
      `--no-ci-check` skips it aloud; the consumers' half is in Downstream) A release refuses while CI on HEAD is red. Baseline: ztools CI has been red since
      2026-10-10 (`.python-version` is gitignored and ci.yml reads it), and v3.6.0, v3.7.0 and
      v3.8.0 were each tagged over it. Exit: the house release step reads the CI conclusion for
      the commit it tags and refuses red or pending, naming the run. Red-first: a fixture remote
      reporting a failed run for HEAD refuses the tag. Lies: a repo with no CI reads as "no runs",
      which is not green -- it is stated as "no CI", and a repo that has CI must not read that way.
- [x] 6.6 (`a03d5fb`: a byte-identical retired stock hook fails structural with `install.sh "$PWD"`;
      nothing is rewritten in place, since `.githooks` is tracked; the consumers' half is in
      Downstream) A stale stock hook goes red, or replaces itself. Baseline: "pre-commit/pre-push is an
      OLDER stock hook" prints in scripts, necrohand, apes, old_phones and Adv360 and never fails
      anything (Downstream records 23 of 29 off-stock at `2fb2901`). Exit: an older stock hook
      fails structural with the reinstall command, or the hook updates itself from `$GOH_DIR`
      when it is a pure stock copy. Red-first: an older stock copy installed in a fixture reds.
      Lies: a hook a repo has edited is not stock -- replacing it destroys the edit, so only a
      byte-identical older stock copy is replaced, and an edited one is named.
- [x] 6.7 (`18330c7`: the stock pre-commit only delegates to `gates/commit_gate.sh`; structural.sh
      marks its scope and tree in `GOH_STRUCTURAL_MARK`, and the commit and push gates run it
      themselves, naming the gap, when the repo's gate passed without it) A consumer's gate that skips the house layer says so. Baseline: old_phones
      `tools/gate.sh` never calls `structural.sh`, so emoji and line-cap are not enforced there;
      its `preflight.sh` is 523 lines and fails the cap when run by hand. Exit: the stock hooks
      run structural themselves (or refuse when the repo's gate did not), so no consumer gate can
      omit it. Red-first: a fixture `gate.sh` that skips structural leaves a planted emoji red.
      Lies: a gate that calls structural with an empty scope "ran" it -- 6.1's empty-scope
      refusal applies.
- [x] 6.8 (`bfac28b`: `tools/release-kit/tap_bump.sh` clones, rewrites, runs the tap's
      `tools/gate.sh --full` in the clone and pushes pinned with a read-back; a red gate pushes
      nothing; `release.sh`'s tap step calls it; the products' half is in Downstream) A tap bump goes through the tap's gate. Baseline: five products' release scripts push
      homebrew-tap bumps straight to GitHub, so the tap's own gate never runs. Exit: one house
      tap-bump step (clone, edit, the tap's gate, pinned push) the five scripts call instead.
      Red-first: a bump that breaks the tap's formula lint is refused before the push. Lies: a
      bump pushed by a token from CI skips local hooks by construction -- the step runs the gate
      itself rather than relying on a hook.
- [x] 6.9 (`496b6a5`: at full scope each file is held to `early_exit_pipe_seed.json` through
      `crate::seedcount`; the consumers' half is in Downstream) An early-exit pipe under pipefail refuses everywhere, not only where opted in.
      Baseline: Finance's gate scripts hold 16 pipes the house checker warns about and passes;
      under pipefail a `| grep -q` can turn a match into a miss. ZoneWM refuses them only through
      `GOH_NO_EARLY_EXIT_PIPE=1`. Exit: on by default with a shrink-only per-repo seed. Red-first:
      a planted `set -o pipefail; yes | grep -q y` in a fixture reds with no opt-in. Lies: a pipe
      whose left side cannot outlive its reader is safe, and the seed is where that is said.

- [x] 6.10 (`b85e0af`, 2026-10-05, which put build scripts with no source suffix under the cap;
      CadGoose2's file last grew on 2026-09-23, before it. Pinned by
      `test_a_commit_growing_an_over_cap_build_script_is_refused_at_commit`.) The line cap holds at commit, not first at push. Baseline: CadGoose2's
      `CMakeLists.txt` has been 1142 lines since 2026-09-23 and no commit refused it; only the
      full-scope pre-push did ("[file_length] 1 file(s) over the 500-line cap"), so a file that
      grows across commits passes each commit and then blocks every push. Exit: the staged
      `file_length` judges every staged file by the same scope and types as `--full`, and a file
      already over the cap that a commit touches is refused there, naming the cap. Red-first: a
      fixture commit that takes an over-cap `CMakeLists.txt` from 1141 to 1142 lines reds at
      `--staged`. Lies: re-read why the staged run missed it before fixing (a type filter, a
      baseline, or only the touched hunk) -- each is a different fix.
- [x] 6.11 (push half: `13f11b4`, `lib/repo_writes.sh` -- the push gate refuses a run that wrote
      the repository's `.git/config` and names a moved ref; commit half: `8d83c85`,
      `gates/commit_gate.sh` compares the same around the gate, pass or fail, now that the stock
      pre-commit delegates to it instead of exec'ing the repo's gate) A hook run never writes the repository it runs in. Baseline: the games metarepo's
      `origin` was `https://elsewhere.invalid/x.git` from 2026-10-08 19:09 until the consolidation
      repaired it: a probe in `82802c0`'s own hook run wrote it into the REAL `.git/config`, and
      nothing compares remote URLs before and after a hook. Sibling: scripts'
      `test_flatten_git_history.sh` writes into the real `backup/flatten-records`. Exit: the
      stock hooks snapshot `.git/config` (remotes included) and the ref set before the gate and
      refuse a run that changed them, naming the key; tests run against scratch repos only (4.1,
      4.2, 6.1). Red-first: a fixture gate that runs `git remote set-url origin
      https://elsewhere.invalid/x.git` is refused and the URL is reported. Lies: a hook that
      fetches legitimately moves remote-tracking refs -- the snapshot excludes `refs/remotes`
      and says so.
- [x] 6.12 (`72e8445`: `rust_gate.sh` runs `cargo audit` with the four `--deny` classes on the
      command line, from the workspace root, outside every proven record; `check_audit_ignores.py`
      refuses an exemption with no reason or one that no longer fires; the consumers' half is in
      Downstream) `cargo audit` is a step of the house Rust gate, configured to bite. Baseline: the
      games repos run no audit at all, and nothing here offers one (no `cargo audit` in `gates/`
      or `crates/goh`); game_asset_factory asked for it here so every consumer inherits it. Exit:
      a Rust-layer step running `cargo audit` with `[output] deny = ["warnings", "unmaintained",
      "unsound", "yanked"]` (and `quiet`), an ignore list that is a shrink-only ratchet with a
      reason per entry and stale entries refused. Red-first: drop one ignore entry in a fixture
      and the step reds. Lies: `cargo audit` exits 0 on unmaintained/unsound without the deny
      list -- a step without it passes over exactly what it was added for.
- [x] 6.13 (`3dccae2`: `scripts/estate_audit.py` audits every tracked lockfile under ~/Projects and
      records it; `hooks/claude/estate_advisories.py` prints new advisories, a summary of standing
      ones, and a stopped job at every session start. Scheduling it (`--install`) and wiring the
      hook into `~/.claude/settings.json` are the owner's) A new advisory is reported before a push, not during one. Baseline: RUSTSEC-2024-0449
      (unsafe-libyaml via serde_yaml_ng) turned antiknob's and media_server's pushes red at the
      same moment with no code change -- the gate working as designed, found at the worst time.
      Exit: a scheduled estate audit (6.12's step over every Rust consumer, daily) that reports a
      new advisory with the crates and repos it reaches, so it is fixed before the next push
      meets it. Red-first: a fixture advisory DB with a new entry against a fixture lockfile is
      reported by the scheduled run. Lies: a report nobody reads is the old state with extra
      steps -- it lands where the owner already looks, and its absence (the job not running) is
      itself reported.

### Phase 5 -- the measurements, last (ids kept from v0.25.0's Phase 4)

Correctness outranks, and no figure is worth a red gate: these move behind 1-4 because every
number they take is taken through a pipeline that cannot currently run (1.1). 5.3 comes first in
this phase: 5.1's exit is a wall time on a box that will not be quiet, so it is restated in 5.3's
figure once that is calibrated.

- [ ] 5.1 (was 4.2) media_server push, everything changed, warm. Baseline: 170 s at load 4-8, BEFORE the
      incremental coverage build (`06afca4`: 173 -> 76 s on this repo). Measured 2026-10-08
      12:47 under a hold: 208 s, controls 0.30/0.30 s -- the exit is NOT met. Five coverage
      steps are 110 s of the step sum (11-31 s each); clippy, 8 s a crate, comes next. Exit:
      <= 90 s. Red-first: the P0 instrument's per-step sum within 5% of wall on the same run.
      Lies: a cold sccache; a crate the "everything changed" diff did not touch; media_server's
      own 110 s pytest step (a servers item) counted as goh's.
- [ ] 5.2 (was 4.3) The P2 budget curve, `GOH_CI_JOBS`, this repo and routines. Baseline: 186/147/138 s
      (1/2/4, a busy box). Measured 2026-10-08 under holds, every gates_of_heck row at ONE pinned
      commit (`2783f0d`; main moved under the unpinned rows): j1 271 s; j2 238 and 236 s; j4 236 s
      (a noisy 262 s re-run clean); j6 noisy twice (233, 260 s). routines at `852b8d5`: j1 174,
      j2 162 s, j4 161 and j6 165 s (both just noisy). **THE KNEE IS j2 in both repos**: -13%
      here, -7% there, nothing after. Red-first met (j1 is slower than j4). Exit: the
      default set at the knee -- `docs/config.md` keeps it 1 because only a repo knows which of
      its steps write one file, so the knee likely belongs in each repo's `.gatesrc`. THE OWNER'S
      CALL, still open. Red-first: a run at jobs=1 no faster than jobs=4, or steps serial
      somewhere. Lies: one repo's knee read as every repo's.
- [ ] 5.3 (was 4.4) A floor stated on a box that is never quiet (O41b, from servers 2026-10-08: "there
      won't be a quiet box for the foreseeable future"). Baseline: media_server on a loaded Mac
      (load 10-74): `tools/repo_tests.sh` ran ~190 subprocess-heavy tests serially, 206-239 s of
      every push and commit -> 51 s at load 56 with xdist (servers `1548344`; 21 s on .33); whole
      push of `1548344` 190 s at load 33-74; per-crate coverage 203 s summed over 28 crates
      (mediaops 50, healthcheck 43, mcp-host 38), musl clippy 54.5 s. Exit: a per-step figure holding within 15% across two loads
      at least 2x apart (CPU per step, or wall normalised by a same-run control), or a named quiet
      second host (.33, 16 cores, load 1-3; no gates_of_heck checkout yet); then per-crate coverage
      judged for running across crates at once. Red-first:
      the chosen figure, for one fixed step at two loads, moves less than wall time does, or it
      normalises nothing.
      Lies: CPU time blind to a step that waits (a lock, the network); a second host's figure read
      as this one's. Instrument landed: `cpu_ms` (`e3e5418`). First point 2026-10-08
      20:28, warm over a pinned `e3e5418`, load 110-132: wall 14.2/15.4/16.7 s, cpu
      7.04/7.22/7.34 s (spread 23% vs 4%). The second load (<= 55) is still owed.

## Open -- measurements (wall times, taken under holds; 5.3 restates them)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands.

The suite target is met; its remaining levers (fewer processes per test, `goh.sh` resolution,
the per-step wrapper) are recorded in the CHANGELOG. Every other row is Phase 5's.

| what | baseline | last | target |
|---|---|---|---|
| this repo's suite, `-n 12 --dist loadgroup` | 94.4 s (v0.20, `-n 8`) | **52.5-53.8 s at load 11-17 (2026-10-06)**, 1987 tests, longest-first (`tests/_schedule.py`); 62-67 s before it on a quiet box | <= 60 s: **MET** |
| media_server push, one crate changed, warm | 197 s | 183 s at load 7-12 (2026-10-06): **110 s is media_server's own pytest suite** (134 tests, run by its gate.sh outside the proven cache); 5 of 29 crates re-gated -- 3 correctly (path users), healthcheck-rs on the whole tree by design (its tests read the repo root), vpn-watchdog-rs never recorded (fixed in `d384c0d`) | <= 30 s: unreachable while the pytest step runs unconditionally; servers `1548344` cut that step to 51 s at load 56 (xdist), not yet re-measured as a push |
| media_server push, everything changed, warm | 177 s (P0) | 170 s at load 4-8 (2026-10-06), before `06afca4` made the instrumented build incremental | <= 90 s: Phase 4.2 re-measures |
| any consumer's pre-commit structural layer | ~1 s | media_server, one staged `.rs`: 0.22 s at load 17 (2026-10-06) | <= 0.4 s: **MET** |
| P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines | 186/147/138 s (1/2/4, busy) | provisional | the knee, recorded |

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

Verified read-only at each repo's committed HEAD on 2026-10-08 (the sha in brackets) unless a
later date is given; an
item marked fixed from a session's word is not pruned. Done and pruned: antiknob's vendored
`_gitutil.py`, antiknob's and divoom's `lock_guard.sh`, antiknob's CI install, ztools' exempt key,
Finance's `"$root.out"`, ZeroThunder's dropped process handle.

- **ZeroThunder, before 3.4:** delete its own `tools/check_subprocess_stdin.py`, repoint
  `tests/e2e/verify.py:290` + `tests/e2e/gate_tiers.py:61`. It is already a NAMED VIOLATION
  ("a vendored copy of a house checker -- delete it and run the shared one"), and its copy and the
  house gate hold two rules for one class: both local checkers want a comment above `stdin=None`,
  the shared native check accepts any. Settle that in 3.1 or state the difference.
- **CadGoose, after `4686280` (6.3):** `goh secrets` refuses
  `.github/workflows/build_and_release.yml:154` (the tap clone puts `${TAP_TOKEN}` in the URL).
  Clone without it and pass the token as `git -c http.extraheader="AUTHORIZATION: basic ..."` or
  through a credential helper reading the variable; 6.8's house tap-bump step replaces the line.
- **scripts, after `13f11b4` (6.11):** `test_flatten_git_history.sh` writes the real
  `backup/flatten-records` ref; the push gate now names it on every push. Build the history in a
  scratch repository (`_gitutil.scratch_git`) instead.
- **Nine repos, after `496b6a5` (6.9), red at their next push until they act:** old_phones (99
  early-exit pipes), koffee_big (29), Finance (16), scripts (12), koffee_oss (11), apes (7),
  CadGoose (7), remote_dotfiles (3), ztools (1), counted 2026-10-12. Fix each
  (`out="$(producer)"; grep -q PAT <<< "$out"`, the shape every finding prints), or adopt what is
  left with `goh early-exit-pipe --seed` and commit `early_exit_pipe_seed.json`, which may only
  fall. By owner ruling (2026-10-12) they stay red until their own sessions take them.
- **The 17 repos in 3.3's table**, one session each: run the check, fix or `--seed`.
- **necrohand, CadGoose2 and ZeroThunder, after `85d0b51` (6.1):** necrohand and CadGoose2 replace
  `tools/check_git_env.py` with `python3 "$GOH_DIR/checks/check_git_env.py"` and move
  `git_env.SOURCES`, `CENSUS_EXEMPT` and `_SHELL_SITES` into `tools/git_env.json` (`sources`,
  `exempt`, `shell`); their `git_env.run` seam stays. necrohand also deletes the stale
  `check_mcp_server.py` excuse the empty-scope sweep names. ZeroThunder's
  `tests/test_hook_git_env.py` stops typing the hook variables and takes
  `_gitutil.local_env_vars()` (GIT_PREFIX is the one its list missed).
- **Eight repos, after `1c4be61` (6.2), red at the next push until their hooks and gate run Python
  through the seam:** a bare `python3` in `.githooks/*` or `tools/gate.sh` is refused. Counted
  2026-10-12 (calls): ZeroThunder 14, app_updates 10, Finance/salary 3, Finance/zinc-core 3,
  games 2, monitor 2, scripts 1 (ZoneWM's one is fixed in its own tree). The fix is mechanical:
  `python3 X` becomes `bash "$GOH/gates/python.sh" X`. A repo whose tools need a library (necrohand's
  spacy) pins the version that has it: a committed `.python-version`, or a `.venv`, which the seam
  prefers and checks against the pin.
- **ztools, after `5c5868e` (6.5):** its next release through the kit refuses while its CI on
  HEAD is red (red since 2026-10-10: ci.yml reads the gitignored `.python-version`). Fix the CI;
  `--no-ci-check` exists, says so aloud, and is not the fix. sys_updater has no workflow and
  releases with "no CI" stated.
- **divoom-control, monitor, antiknob, CadGoose and ztools, after `bfac28b` (6.8):** each pushes
  its homebrew-tap bump itself (`scripts/release.sh`, `release.sh`, `release.sh`,
  `scripts/push_release.sh`, `tools/release.sh` with `tools/upgrade_tap.py`), so the tap's gate
  never runs on it. Replace that step with `$GOH_DIR/tools/release-kit/tap_bump.sh --tap
  ztomer/homebrew-tap --formula|--cask NAME --tag vX.Y.Z --artifact PATH|URL`.
- **Every consumer, after `a03d5fb` (6.6) and `18330c7` (6.7), red at its next commit until it
  re-installs:** 6.7 retired the stock pre-commit that every repo carried, and 6.6 refuses a
  retired one. Run `~/Projects/gates_of_heck/install.sh .` and commit `.githooks/pre-commit` and
  `.githooks/.goh-installed/pre-commit.sha256`. Fourteen were already a version behind on
  2026-10-12 (Adv360-Pro-ZMK, apes, games, CadGoose2, ZeroThunder, game_asset_factory, necrohand,
  Finance/ZincTender, homebrew-tap, koffee_oss, old_phones, remote_dotfiles, scripts, sys_updater);
  the same one command covers them. This is the last such wave for commit-time logic: the hook now
  decides nothing. A repo whose `tools/gate.sh` skips structural (old_phones) will see the house
  layer run after it on every commit, the gap named, until its gate calls it.
- **antiknob and media_server, after `72e8445` (6.12), red at their next push:** RUSTSEC-2024-0449
  (unsafe-libyaml 0.2.11, unmaintained, via serde_yaml_ng) fires in antiknob's `Cargo.lock` and in
  eight media_server crate lockfiles (healthcheck-rs, lan-ip-watcher-rs and its testkit, mcp-host-rs,
  mediaops-rs and its testkit, vpn-watchdog-rs, yaml-mini-rs), counted 2026-10-12. Move off
  serde_yaml_ng, or ignore the id in the workspace root's `.cargo/audit.toml` WITH a `# reason` on
  its line -- media_server's file sits at the repo root, where cargo-audit, run from each crate's
  workspace root, never reads it. Every other estate lockfile the gate runs on was clean.
- **games metarepo and four nested repos, after `92b0946` (4.1):** `tools/check_plan_refs.py:131`,
  `tools/check_phase_exits.py:82`, `tools/check_plan_claims.py:117` and `:168` ask
  `os.path.isdir(<repo>/.git)`, False in a linked worktree (`:168` fails OPEN: a claim in such a
  repo reads as tracked) -- call `_gitutil.is_work_tree`. The census copies delegate to
  `_gitcensus.census(root, sources, exempt)`: CadGoose2 `tools/git_env.py:144-224` and necrohand
  `tools/git_env.py:152-240` (already by callee; keep their `CENSUS_EXEMPT` and shell-site lists),
  gaf `tests/test_hook_git_env.py:132-168` (matches the ARGV, so its seam file must be exempt to
  pass), ZeroThunder `tests/test_hook_git_env.py:228-300` (a substring per FILE: a file that calls
  `run_git(` once is cleared of a raw call beside it). Each keeps its own floor on `read`, and
  `stale` replaces ZeroThunder's hand ratchet.
- **Every census consumer, after `dde34ef` (4.2):** `census(...)` now also returns `fixture_sites`
  (a raw spawn with a `cwd` and the caller's environment: none, `os.environ` or a copy) and
  `stale_inherit`; assert both empty beside `sites`, declaring with `inherit={path_or_dir: reason}`
  only what runs in the gated repo or under a process that drops git's local variables first. The
  shapes pinned are gaf's `run_check` and ZeroThunder's `emoji_rejected`; an `env=<name>` built
  elsewhere is trusted, so a name built from `os.environ` is the remaining blind spot.
- **ZeroThunder, the D1 goldens work (2026-10-09):** 7 modified files and 1 new
  (`Core/AppDelegate/GoldenFrameDelivery.swift`) are UNCOMMITTED in its tree, plus a worktree at
  `/private/tmp/zt-gold`; break-probes 1 and 2 red in HeadlessTests, break 3 (the guard removed
  from the loop) caught by no unit test, and the machine was in the lid-closed arm, so its 15/15
  says nothing about display independence. ZeroThunder's to land or drop.
- **After the Python 3.15 move (2026-10-10, Antigravity `460d804a`):** divoom-control
  `.github/workflows/tests.yml:45,111` and antiknob `.github/workflows/tests.yml:75` still pin
  3.14 for jobs that run this repo's gates; scripts `tests/test_proc.py:211` looks for coverage
  `.pth` files only in `site.getsitepackages()`, so the user-site `a1_coverage.pth` is missed
  (266 vs 267); necrohand's 5 spaCy modules run only on 3.14; Finance/zinc fails on 3.15
  (PyMuPDF imports `typing.ByteString`, and the repo makes warnings errors).
- **ztools, after `3103441`:** source `$GOH_DIR/lib/gpu_lock/gpu_lock.sh` and delete
  `tools/gpu_lock.sh` (its header cites a desktop-lock path that is gone); point
  `rust/tests/gpu_lock_shell_parity.rs` and `tools/tests/test_gpu_lock_shell.py` at the house
  file, so the Rust client's parity is judged against the one bash half. Hooks reinstalled
  2026-10-10 (`79698b2`).
- **scripts:** `lib/test_desktop_lock.sh` (15110 bytes) is a second, drifted test of this repo's
  `lib/desktop_lock/` (whose own is 6472): fold what it covers that ours does not into ours, then
  delete it.
- **ZoneWM:** `.gemini-workspace` points at `docs/GEMINI.md`, `docs/ARCHITECTURE.md` and
  `docs/CONTRIBUTING.md`, none of which exist; its census sittings hold the house desktop
  and GPU locks since `90336053` (D-0260); the single probes still hold only `~/.zonetiler-qa.lock`.
- **necrohand and the games metarepo, after `b0571d7` (4.5):** each runs pyflakes beside ruff
  (`tools/check_pyflakes.py`, `tools/repo_gates.sh`); set `GOH_PY_NOQA_PARITY=pyflakes` in
  `.gatesrc`. Measured 2026-10-12, both clean (necrohand's 133 tracked `.py`, the metarepo's 13 in
  `tools/`), so opting in turns nothing red; or drop pyflakes for ruff's F rules, one vocabulary.
- **Every INSTALLED repo:** re-run `$GOH_DIR/install.sh <repo>` (done 2026-10-10: ztools, Finance); `structural.sh` names an older
  stock hook and ZeroThunder warns on every gate run. At `2fb2901` 6 of 29 matched the stock
  pre-commit, 23 did not; servers was reinstalling then -- re-check. **A Rust toolchain is required**
  for layer 1 (`bin/goh` is the only tier). After `10be7e8` a reinstall also adds `post-rewrite`
  and `pre-applypatch` (the conflict-marker check for rebase picks and `git am`);
  `gate.sh --doctor` names each stock hook a repo lacks.
- **Every repo, after v0.24.0 (nothing to do):** rust coverage runs once per crate; a crate whose
  build reads in-repo files outside its scope is recorded from its second run; the step wrapper
  costs half. Servers told 2026-10-08; ztools not yet.
- **Over the line cap since build files entered scope:** CadGoose [`e1fa2b3c`] `CMakeLists.txt`
  (702), games/CadGoose2 [`1a3f659d`] `CMakeLists.txt` (1142, re-measured 2026-10-09, still over),
  games/necrohand [`fae673d3`] `Makefile` (511 at the time; the ruff sweep brought it to 475, so
  CadGoose2 is the only one left). No exclude names them.
- **Callers of a retired Python checker by path** (forwarders keep working): ZoneWM [`d2aa402b`]
  `tools/release_precheck.sh:35`; app_updates [`22ec4aa2`] `tools/gate.sh:35,51`; koffee_big
  [`9e375e37`] `tools/gates_lint.sh:28,30,131`; Finance/salary [`e553bf2f`], Finance/zinc-core
  [`972bcd47`] `tools/local_ci.sh`; CadGoose2 [`1a3f659d`] `.gatesrc:37`; necrohand [`fae673d3`]
  `Makefile:80,254`. Move to `bash "$GOH_DIR/gates/goh.sh" <check>`.
- **monitor** [`b690cfc8`]: CI still runs `GOH_NO_NATIVE=1 ... structural.sh --full` (`ci.yml:67`;
  the key is retired and ignored, `.gatesrc:44`, `ci.yml:119`) and a plain `cargo test --workspace`
  beside the gate's. Its customised hooks are monitor's call.
- **routines** [`a4712bcc`]: `.gatesrc:26` still runs plain `cargo test --all-features --locked`
  (keep `--doc`; P1c landed in v0.21.0).
- **ztools** [`efe364a1`]: `rust/` done; `vendor/camoufox-rs` still runs a plain `cargo test` and
  carries 24 `assert!(..is_empty())` (rust/ 1). `GOH_EXCLUDE='^vendor/'` is set. Not told of v0.24.0 or
  v0.25.0, and 13 undeclared stdin calls (3.3).
- **servers / media_server** [`a1a1d9ac`]: `scripts/dev/check.sh` step 6 runs pytest every push
  (`1548344` cut it 206-239 s -> 51 s at load 56 under xdist, and found the cost was serial
  execution rather than repetition, so a scoped proven cache for the repo tests is no longer
  needed); `tools/gate.sh:88` still loops crates with `xargs -P 4`: adopt `rust_gate.sh
  --each-crate` and `GOH_RUST_LINT_CARGO=cargo-zigbuild` (v0.22.0).
  88 undeclared stdin calls (3.3).
- **CadGoose2** [`a465180`]: `CMakeLists.txt` is 1142 lines against the 500 cap and aborts layers
  2-3 under `gate.sh --full`'s `set -e`; no exclude names it; split, do not exempt.
- **divoom-control** [`0ac21228`]: CI installs with `required_tools.py --layer structural --names`
  plus apt, not `--repo . --install`.
- **ZoneWM** [`d2aa402b`]: `goh requires-call` expresses `check_probe_placement.py` and both halves
  of `check_probe_courtesy.py` as rows; `.gatesrc` sets no `GOH_REQUIRES_CALL` and both copies are
  there -- 5 of its 17 LEGITIMATE entries would go stale as rows. Deleting them is its owner's call. 118 undeclared stdin calls (3.3), and
  `check_focused_border_verdicts.py --probe` fails alone at that HEAD (a fill-arm assertion).
- **Every consumer pushing by branch name over HTTPS:** `push_gate.sh` refuses a ref that moved
  while it was gated; ZoneWM's pinned `git push <remote> <sha>:<branch>` is the airtight form.
- **O35 (servers):** a per-consumer `gate_calibration.json` registry; a feature here, unscoped. Re-reported
  open by the 2026-10-12 consolidation; servers carries a pointer to it.

## Known limits, stated so they are not rediscovered

### The stdin gate, as ported

- **Python only.** No shell, no Rust, no Swift, no JS/TS, and no Python embedded in a shell
  heredoc -- two such sites are already known (CadGoose2 `tools/ab_goose_overlay_fps.sh:33`,
  `scripts/package_macos.sh:199`) and no tracked-`.py` gate can reach them. Measured empty for
  Swift in ZeroThunder (419 files, no `Process`/`NSTask`).
- **One level of attribute.** `runner = subprocess.run; runner([...])` is invisible, by design
  (`calls.rs:30`): reaching through an unresolved attribute is how a port invents findings nobody
  has to fix. **`stdin=None` is legal** ("inherit, on purpose") -- while two repos' local checkers
  demand a comment above it, so two rules for one class until 3.4 settles it.
- **A Python 2 file is an error, not a skip** (measured on a third-party clone). Correct as a
  policy, wrong as a dialect for the scanner.
- The scope is `git ls-files --cached --others --exclude-standard`: TRACKED PLUS untracked-not-
  ignored; two estate repos carry untracked Python in scope today.
- The ratchet machinery is unit-tested both ways, but with zero seeded entries anywhere it has
  never been exercised against real debt.

### `goh unreaped-spawn` + `lib/orphan_canary.py`

- A child that calls `setsid()` leaves the step's process group, the canary's only evidence
  (`ppid == 1` cross-reported every concurrent gate). Needs its own signal, e.g. a helper pidfile.
- Per-function, not interprocedural: a spawn RETURNED is a handoff (four in the estate). Same-file
  callees that can panic are read as panicking; other files and crates are not. Cross-CRATE guard
  types are reported `unjudgeable`, counted and named, never failed on.
- A kill inside a conditional (`if cond { child.kill(); }`) is accepted. Cost unknown; needs a
  corpus. Rust, Python, shell only: no `.swift`/`.ts`/`.go` test spawns in the estate today.

### The C2 writer hook and the commit gate

- `hooks/claude/skill_edit.sh` judges a Bash command that NAMES the corpus (5.1); one that writes
  it without naming it (a `cd` there first, a variable) is judged at the next commit or push.
- The commit gate is the STAGED structural layer by design; the suite runs at push.

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
- A sibling-repo seam for a gate whose evidence lives in ANOTHER repo: `GOH_CROSS_REPO_ROOT` is
  set BY `push_gate.sh` and is deliberately not a symlink into the worktree, so ZeroThunder had to
  invent a per-repo `GAF_DIR` -- which RAISES when absent, because "no checkout" must never read as
  "uncalibrated". One house pattern, once a second repo needs it.
- The `git archive` export has no `.git`, so `structural.sh` cannot judge one (it reads the index).
  A clone is the faithful measurement; re-open only if an export ever has to be gated.
- **antiknob, divoom-control, routines, ztools, after `51109d6` (4.9):** call
  `$GOH_DIR/gates/goh.sh status-markers` from the repo's gate, fix what is cheap and `--seed` the
  rest. At adoption (2026-10-11): antiknob 113 sites in 9 files (Rust `src/bin/`, `install.sh`,
  `ui/build.sh`; its Rust waits on 4.14), divoom-control 52 in 11, routines 3 in 1, ztools 3 in 1.
  ZoneWM reads 0 and adopts it with no seed.
- **The starter's tilde-path comment (`c8e8b4b`), done 2026-10-12 by the consolidation session:**
  edited in every repo that carried it; pushed everywhere but remote_dotfiles `6da73a0` (no remote, by
  the owner's ruling) and antiknob `cafd84f` (its pre-push is held by RUSTSEC-2024-0449). Both trees
  have the fix, so a corpus read from disk is clean.
- **antiknob, after `a74bbff` (4.14):** its Rust `src/bin/` sites can print through `goh-tui`:
  `goh-tui = { path = "../gates_of_heck/crates/goh-tui" }` (Cargo reads no env var in a path, so the
  sibling checkout IS `$GOH_DIR`; its `build.rs` reads the stylerc beside it, never a copy), then
  `goh_tui::outln!("{OK} ...")` in place of each `[ Ok  ]`, and re-seed `status-markers` smaller.
  Re-measured 2026-10-12 by the consolidation: 43 hand-rolled `[ ==> ]` prefixes left in its Rust binaries.
