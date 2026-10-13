# BACKLOG — the roadmap, and every open item (rule #16: one forward-looking file)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-13, v0.26.0 released (read first)

v0.26.0 (2026-10-13) closed the v0.26.0 roadmap except the items below: Phases 1, 2, 4 (all but
4.3 and 4.6) and 6 landed, and the stdin gate landed opt-in with this repo's own row of the sweep
closed. What landed, with its commit and what was wrong before, is the CHANGELOG stanza; the
landed plans themselves are in git history (this file at `6522f05`). Box: 16 cores, 4-5 busy at
idle, sys ~= user, so **spawn count, tree walks and network round trips are the cost metric, not
CPU**. The box is never quiet for the foreseeable future (the owner, 2026-10-08; load 10-132 seen
since), so a figure is a step's own work (`cpu_ms`, a count) or a same-run comparison, never a
quiet wall time (5.3). One lesson from v0.25.1 still governs any measurement work: **on this box,
a string comparison of a path is a measurement of the CALLER, not of the filesystem**.

## Resume here (2026-10-13)

- **Next, in order:** 4.6 (the frames-drawn invariant; 4.3 landed at `831d686`), then 3.3's remaining rows as their repos' sessions take them, 3.4's flip once 3.3's
  table is empty, then the measurements (5.3 first, then 5.1, 5.2).
- **Waiting on the owner, and nothing here moves them:**
  - 6.13's scheduling: `scripts/estate_audit.py --install` writes and loads the daily launchd job.
  - Registering `hooks/claude/estate_advisories.py` (SessionStart) in `~/.claude/settings.json`.
  - `GOH_RED_RUN` turned on, per repo: no repo has it on yet (`e526df9`).
  - 5.2's `GOH_CI_JOBS` default: `GOH_CI_JOBS=2` per repo, or the default left at 1.
- **Main moves only through `tools/land.sh`**, and since `7e26571` this repo's push refuses a
  `main` tip land.sh did not gate (`GOH_LANDED_ONLY=main`). Every Rust change runs
  `cargo test --workspace` before its commit, and the suite runs before a commit to gate or test
  code.
- **The machine is the owner's to use** (2026-10-10), apart from other sessions doing their own
  work: a measurement may hold the desktop and the GPU for as long as it needs, and the box is
  still never quiet, so 5.3's rule stands.
- Consumers told of a tag: servers and ztools only (the owner's call, 2026-10-08, for quota).
- Owed from 2026-10-08, unchanged: servers' `push-gate-build-dir-config` verified against servers
  (its content is here at `d98d7bd`); 5.3's second calibration point at load <= 55 (method in
  5.3).

## Roadmap to v0.27.0 — the plan of record (re-phased 2026-10-13)

Ordered by what is open: correctness first (two classes the estate found that no gate closes
yet), then the stdin campaign, whose flip waits on its sweep, then the measurements, which no
correctness item waits on. Item ids are kept from the v0.26.0 plan so commit messages that cite
them still resolve. Every item carries **Baseline:**, **Exit:**, **Red-first:** and **Lies:**, and
a done item names its commit, or `tests/test_backlog_items.py` fails; a landed item is pruned to
the CHANGELOG. `[ ]` open, `[x]` done, `[~]` handed off.

### Phase 1 — correctness: the classes no gate closes yet

- [x] 4.3 Every scanner declares its subject, so a scan that stopped reading says so (`831d686`).
      Baseline was one floor key (`GOH_SUBPROCESS_STDIN_MIN_CALLS`) and nothing for any other
      scanner. Now 15 keys, one per scanner, in one registry (`crates/goh/src/subject.rs`): the 12
      always-on structural scanners record what they read and the pipeline's last step judges each
      at full scope (red under a floor, naming key, floor and count; exit 2 naming an unparseable
      key; one advisory line `KEY=N` for every scanner with none), and `goh fixed-sleep` /
      `goh status-markers` judge their own. The pairing: under its floor a seed's stale verdict is
      withheld and `--seed` refuses; over it a stale entry still fails. Red-first measured on
      fixtures (`tests/test_subject_floors.py`: every `.py` excluded reads NONE; a floor one over
      the count is red). This repo sets every floor at about nine tenths of its 2026-10-13 count.
      Not covered, by what they read (named in `docs/config.md`): tracked-ignored and vendored
      (the listing itself), credential-urls, lock-version, provenance (one file each), opt-ins.
- [ ] 4.6 The frames-drawn invariant: a harness that requests N frames reads back N.
      Baseline: ZeroThunder, measured 2026-10-08 -- 14+ harnesses pass `--golden-frames 90`
      (`tests/e2e/golden.py:206`, `golden_bistability_core.py:268`, and 12 more) and NOTHING
      compares frames drawn to frames requested. The golden loop drives its frames through the
      app's ON-SCREEN `MTKView` (`AppDelegate+GoldenReport.swift:238`, left display-linked at
      `AppDelegate+Setup.swift:199`), so with the built-in panel on the view drops every other
      draw -- 45 frames of 2 ticks -- and the resulting arm tracked the WindowServer display log
      at 7 of 7 observations. `golden_bistability.py` exists and is blind to it BY ITS OWN
      DOCSTRING: it catches a latch, and a display-paced render is a systematic half-rate,
      deterministic within a display state. "Pin the view paused" is disproved (it delivers 45 of
      90 frames and `golden.py` went 15/15 -> 0/15); ZeroThunder's uncommitted
      `GoldenFrameDelivery.swift` (requested vs delivered, `exit(2)` on mismatch) is the consumer
      prototype (Downstream). Exit: `crates/goh/src/screen.rs` (tests touching the screen) and
      `checks/check_display_seam.py` (app source presenting outside a declared seam) grow a third
      rule -- an offscreen render paced by an on-screen view -- and a harness that requests N
      frames fails unless it reads N back. Red-first: a fixture rendering at half rate must red
      on the COUNT, not on a pixel. Lies: a harness that reads back a constant is not measuring;
      the count must come from the renderer that drew. Consumer-side, named there:
      `ZeroThunder/docs/ROADMAP.md:523` still carries a `CLOSED 2026-10-01` whose stated premise
      was measured wrong, and the metarepo's claims registry holds a `plan.golden-pass` truth that
      is a function of the HOST's display state -- it went DRIFTED the moment the lid closed.

### Phase 2 — the stdin campaign: the sweep, then the flip

The ruling from the estate's sessions stands: **hooks run whatever is in this checkout, so the
gate turns on everywhere last -- every repo gets its fix or its ratchet first.** The migration
path exists (`--seed`, `498eb9a`), and a declaration is mechanical where no call needs the
caller's stdin (goh's 555: an AST pass placing `stdin=subprocess.DEVNULL` at each call's closing
paren, from the `--source` JSON's line, then `ruff format`).

- [ ] 3.3 The estate sweep. Baseline: MEASURED 2026-10-09 with this tree's own binary, scope
      `git ls-files --cached --others --exclude-standard`: 34 repos carry Python, 1361 process
      calls, **996 findings**, 17 repos exit 1. Two rows are closed (2026-10-13): gates_of_heck
      (511 then, 555 calls in 186 files when declared, `df8f9c2`: no allowlist, 614 calls read,
      0 findings, floor 550 proved red at 10000) and ZoneWM (118 then, its `c8766158`: 123
      declared, floor 110 of 125). **15 rows remain, 367 findings as of 2026-10-09** (re-read each
      before its round): media_server 88, Finance/zinc 71, divoom-control 52, scripts 52,
      monitor 39, koffee_big 23, ztools 13, routines 8, games 6, app_updates 5, old_phones 4,
      Finance/salary 2, Finance/zinc-core 2, CadGoose 1, sys_updater 1. Already swept and clean:
      ZeroThunder 165 calls, necrohand 76, CadGoose2 23, game_asset_factory 41. 10 estate repos
      track no Python and exit 0 "not applicable". The debt is SPREAD (389 files carried it on
      2026-10-09, the largest 16), so each row is a per-file exercise. Each row is its repo's own
      session's (Downstream). Exit: every repo in that table either declares its stdin or carries
      a seeded allowlist at or under its 2026-10-09 count, and sets a call floor. Red-first: run
      the check in a remaining repo with `GOH_SUBPROCESS_STDIN=on` and no allowlist -- exit 1,
      measured 2026-10-09 in all 17. Lies: an exclusion can hide the subject; `GOH_EXCLUDE` is the
      only key this check reads (only media_server's removes Python: 2 vendored files, 0
      findings) and `GOH_LINE_EXCLUDE` cannot affect it at all. A seeded row is debt, not a fix:
      its `unreviewed` count is the number that must fall.
- [ ] 3.4 The flip: `GOH_SUBPROCESS_STDIN` defaults to `on`. Baseline: default `off`; on in 2 of
      34 repos (gates_of_heck, ZoneWM), each with a floor in `.gatesrc`; 18 have a keyless
      `.gatesrc`; ZeroThunder still carries its own `tools/check_subprocess_stdin.py`, which the
      vendored-copy gate already names as a copy to delete (Downstream). Exit: the step is in the
      always-on group at both scopes, and an undeclared call is red at its author's next commit
      in any repo. Red-first: a repo whose Python is excluded away entirely must still say it
      read none; a fixture repo with no `GOH_SUBPROCESS_STDIN` key and one undeclared call reds.
      Lies: flipping before 3.3 makes 15 repos' next commit red for debt nobody has looked at --
      the order is the earlier ruling's. And `stdin=None` is legal here while two repos' local
      checkers demand a comment above it: settle that or state the difference before the flip.

### Phase 3 — the measurements, last

Correctness outranks, and no figure is worth a red gate. 5.3 comes first in this phase: 5.1's
exit is a wall time on a box that will not be quiet, so it is restated in 5.3's figure once that
is calibrated. The instruments: P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`), `cpu_ms`
(`e3e5418`), `tools/quiet.sh` and `lib/bench_hold.py` for holds, and `tools/session_bench.py` for
cross-session serialization (its section below).

- [ ] 5.3 A floor stated on a box that is never quiet (O41b, from servers 2026-10-08: "there
      won't be a quiet box for the foreseeable future"). Baseline: media_server on a loaded Mac
      (load 10-74): `tools/repo_tests.sh` ran ~190 subprocess-heavy tests serially, 206-239 s of
      every push and commit -> 51 s at load 56 with xdist (servers `1548344`; 21 s on .33); whole
      push of `1548344` 190 s at load 33-74; per-crate coverage 203 s summed over 28 crates
      (mediaops 50, healthcheck 43, mcp-host 38), musl clippy 54.5 s. First point 2026-10-08
      20:28, warm over a pinned `e3e5418`, load 110-132: wall 14.2/15.4/16.7 s, cpu
      7.04/7.22/7.34 s (spread 23% vs 4%). The second load (<= 55) is still owed. Exit: a
      per-step figure holding within 15% across two loads at least 2x apart (CPU per step, or
      wall normalised by a same-run control), or a named quiet second host (.33, 16 cores, load
      1-3; no gates_of_heck checkout yet); then per-crate coverage judged for running across
      crates at once. Red-first: the chosen figure, for one fixed step at two loads, moves less
      than wall time does, or it normalises nothing. Lies: CPU time blind to a step that waits
      (a lock, the network); a second host's figure read as this one's.
- [ ] 5.1 media_server push, everything changed, warm. Baseline: 170 s at load 4-8, BEFORE the
      incremental coverage build (`06afca4`: 173 -> 76 s on this repo). Measured 2026-10-08
      12:47 under a hold: 208 s, controls 0.30/0.30 s -- the exit is NOT met. Five coverage
      steps are 110 s of the step sum (11-31 s each); clippy, 8 s a crate, comes next. Since
      then the house Rust gate gained a `cargo audit` step (`72e8445`, under 0.5 s measured):
      unmeasured in this push. Exit: <= 90 s, restated in 5.3's figure. Red-first: the P0
      instrument's per-step sum within 5% of wall on the same run. Lies: a cold sccache; a crate
      the "everything changed" diff did not touch; media_server's own 110 s pytest step (a
      servers item) counted as goh's.
- [ ] 5.2 The P2 budget curve, `GOH_CI_JOBS`, this repo and routines. Baseline: 186/147/138 s
      (1/2/4, a busy box). Measured 2026-10-08 under holds, every gates_of_heck row at ONE pinned
      commit (`2783f0d`): j1 271 s; j2 238 and 236 s; j4 236 s (a noisy 262 s re-run clean); j6
      noisy twice (233, 260 s). routines at `852b8d5`: j1 174, j2 162 s, j4 161 and j6 165 s.
      **THE KNEE IS j2 in both repos**: -13% here, -7% there, nothing after. This repo's
      `GOH_CI_STEPS` has since gained `status-markers` and the path-invariance run (27 s), so the
      curve is unmeasured at HEAD. Exit: the default set at the knee -- `docs/config.md` keeps it
      1 because only a repo knows which of its steps write one file, so the knee likely belongs
      in each repo's `.gatesrc`. THE OWNER'S CALL, still open. Red-first: a run at jobs=1 no
      faster than jobs=4, or steps serial somewhere (met on 2026-10-08: j1 slower than j4).
      Lies: one repo's knee read as every repo's.

## Open -- measurements (wall times, taken under holds; 5.3 restates them)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands. The met rows
(this repo's suite <= 60 s, a consumer's pre-commit structural layer <= 0.4 s) are in the
CHANGELOG.

| what | baseline | last | target |
|---|---|---|---|
| media_server push, one crate changed, warm | 197 s | 183 s at load 7-12 (2026-10-06): **110 s is media_server's own pytest suite** (134 tests, run by its gate.sh outside the proven cache); 5 of 29 crates re-gated -- 3 correctly (path users), healthcheck-rs on the whole tree by design (its tests read the repo root), vpn-watchdog-rs never recorded (fixed in `d384c0d`) | <= 30 s: unreachable while the pytest step runs unconditionally; servers `1548344` cut that step to 51 s at load 56 (xdist), not yet re-measured as a push |
| media_server push, everything changed, warm | 177 s (P0) | 208 s under a hold (2026-10-08, after `06afca4`) | <= 90 s: 5.1 |
| P2 budget curve, `GOH_CI_JOBS` 1/2/4/6, this repo and routines | 186/147/138 s (1/2/4, busy) | knee j2 in both (2026-10-08) | the default: 5.2, the owner's call |

## Watch — cross-session serialization (`tools/session_bench.py`)

Closed at sigma 0.107 for `structural --full` at N=1-8 under a hold (`75f109c`, 2026-10-08; it was
0.51). Re-run it after any change that adds work to the `--full` sweep; above 0.15 re-opens it here.

## Downstream: what each consumer session needs to know

Verified read-only at each repo's committed HEAD on 2026-10-08 (the sha in brackets) unless a
later date is given; an item marked fixed from a session's word is not pruned. Done and pruned:
antiknob's vendored `_gitutil.py`, antiknob's and divoom's `lock_guard.sh`, antiknob's CI install,
ztools' exempt key, Finance's `"$root.out"`, ZeroThunder's dropped process handle, the starter
`.gatesrc`'s tilde-path comment (`c8e8b4b`, edited in every repo that carried it, 2026-10-12).

- **Every consumer, after `a03d5fb` (6.6) and `18330c7` (6.7), red at its next commit until it
  re-installs:** 6.7 retired the stock pre-commit that every repo carried, and 6.6 refuses a
  retired one. Run `"$GOH_DIR/install.sh" .` and commit `.githooks/pre-commit` and
  `.githooks/.goh-installed/pre-commit.sha256`. Fourteen were already a version behind on
  2026-10-12 (Adv360-Pro-ZMK, apes, games, CadGoose2, ZeroThunder, game_asset_factory, necrohand,
  Finance/ZincTender, homebrew-tap, koffee_oss, old_phones, remote_dotfiles, scripts, sys_updater);
  the same one command covers them. This is the last such wave for commit-time logic: the hook now
  decides nothing. A repo whose `tools/gate.sh` skips structural (old_phones) will see the house
  layer run after it on every commit, the gap named, until its gate calls it. A reinstall also
  adds `post-rewrite` and `pre-applypatch` (`10be7e8`); `gate.sh --doctor` names each stock hook
  a repo lacks. **A Rust toolchain is required** for layer 1 (`bin/goh` is the only tier).
- **Every consumer, after `831d686` (4.3), one new line and nothing red:** at full scope the
  structural gate ends with `every scanner read its declared subject` and, while the repo's
  `.gatesrc` sets no floors, one line `[subject] N scanner(s) declare no floor ... this run read:
  GOH_EMOJI_MIN_FILES=637 ...`. Paste each at or a little under its count (`0` declares "no floor
  here"); a value that is not a whole number refuses the whole `.gatesrc` naming the key. ZoneWM's
  `GOH_SUBPROCESS_STDIN_MIN_CALLS=110` is read exactly as before; its below-floor message now
  carries the `[subject]` tag, and a `--seed` under the floor is refused.
- **ZeroThunder, before 3.4:** delete its own `tools/check_subprocess_stdin.py`, repoint
  `tests/e2e/verify.py:290` + `tests/e2e/gate_tiers.py:61`. It is already a NAMED VIOLATION
  ("a vendored copy of a house checker -- delete it and run the shared one"), and its copy and the
  house gate hold two rules for one class: both local checkers want a comment above `stdin=None`,
  the shared native check accepts any. 3.4 settles that or states the difference.
- **The 15 repos left in 3.3's table**, one session each: run the check with
  `GOH_SUBPROCESS_STDIN=on`, declare or `--seed`, set `GOH_SUBPROCESS_STDIN_MIN_CALLS`. A
  declaration is mechanical where no call needs the caller's stdin (the method is in Phase 2's
  preamble; ZoneWM's `tools/proc.py` is a helper for the plain calls).
- **ZeroThunder, after 4.7:** wrap the SwiftLint step in `GOH_CI_STEPS` as
  `python3 $GOH_DIR/gates/path_invariance.py -- <the swiftlint command>`; it clones HEAD and runs it
  from `/private/var/...`, `/var/...` and `/System/Volumes/Data/...`, so the `relativeDisplayPath`
  baseline that read 1034 violations as new from one spelling reds there, not in a scratch run.
- **ZeroThunder, the D1 goldens work (2026-10-09):** 7 modified files and 1 new
  (`Core/AppDelegate/GoldenFrameDelivery.swift`) are UNCOMMITTED in its tree, plus a worktree at
  `/private/tmp/zt-gold`; break-probes 1 and 2 red in HeadlessTests, break 3 (the guard removed
  from the loop) caught by no unit test, and the machine was in the lid-closed arm, so its 15/15
  says nothing about display independence. ZeroThunder's to land or drop; 4.6 is the house half.
- **Nine repos, after `496b6a5` (6.9), red at their next push until they act:** old_phones (99
  early-exit pipes), koffee_big (29), Finance (16), scripts (12), koffee_oss (11), apes (7),
  CadGoose (7), remote_dotfiles (3), ztools (1), counted 2026-10-12. Fix each
  (`out="$(producer)"; grep -q PAT <<< "$out"`, the shape every finding prints), or adopt what is
  left with `goh early-exit-pipe --seed` and commit `early_exit_pipe_seed.json`, which may only
  fall. By owner ruling (2026-10-12) they stay red until their own sessions take them.
- **Eight repos, after `1c4be61` (6.2), red at the next push until their hooks and gate run Python
  through the seam:** a bare `python3` in `.githooks/*` or `tools/gate.sh` is refused. Counted
  2026-10-12 (calls): ZeroThunder 14, app_updates 10, Finance/salary 3, Finance/zinc-core 3,
  games 2, monitor 2, scripts 1 (ZoneWM's one is fixed in its own tree). The fix is mechanical:
  `python3 X` becomes `bash "$GOH/gates/python.sh" X`. A repo whose tools need a library (necrohand's
  spacy) pins the version that has it: a committed `.python-version`, or a `.venv`, which the seam
  prefers and checks against the pin.
- **antiknob and media_server, after `72e8445` (6.12), red at their next push:** RUSTSEC-2024-0449
  (unsafe-libyaml 0.2.11, unmaintained, via serde_yaml_ng) fires in antiknob's `Cargo.lock` and in
  eight media_server crate lockfiles (healthcheck-rs, lan-ip-watcher-rs and its testkit, mcp-host-rs,
  mediaops-rs and its testkit, vpn-watchdog-rs, yaml-mini-rs), counted 2026-10-12. Move off
  serde_yaml_ng, or ignore the id in the workspace root's `.cargo/audit.toml` WITH a `# reason` on
  its line -- media_server's file sits at the repo root, where cargo-audit, run from each crate's
  workspace root, never reads it. Every other estate lockfile the gate runs on was clean.
- **CadGoose, after `4686280` (6.3):** `goh secrets` refuses
  `.github/workflows/build_and_release.yml:154` (the tap clone puts `${TAP_TOKEN}` in the URL).
  Clone without it and pass the token as `git -c http.extraheader="AUTHORIZATION: basic ..."` or
  through a credential helper reading the variable; 6.8's house tap-bump step replaces the line.
- **scripts, after `13f11b4` (6.11):** `test_flatten_git_history.sh` writes the real
  `backup/flatten-records` ref; the push and commit gates name it on every run. Build the history
  in a scratch repository (`_gitutil.scratch_git`) instead.
- **necrohand, CadGoose2 and ZeroThunder, after `85d0b51` (6.1):** necrohand and CadGoose2 replace
  `tools/check_git_env.py` with `python3 "$GOH_DIR/checks/check_git_env.py"` and move
  `git_env.SOURCES`, `CENSUS_EXEMPT` and `_SHELL_SITES` into `tools/git_env.json` (`sources`,
  `exempt`, `shell`); their `git_env.run` seam stays. necrohand also deletes the stale
  `check_mcp_server.py` excuse the empty-scope sweep names. ZeroThunder's
  `tests/test_hook_git_env.py` stops typing the hook variables and takes
  `_gitutil.local_env_vars()` (GIT_PREFIX is the one its list missed).
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
- **necrohand and the games metarepo, after `b0571d7` (4.5):** each runs pyflakes beside ruff
  (`tools/check_pyflakes.py`, `tools/repo_gates.sh`); set `GOH_PY_NOQA_PARITY=pyflakes` in
  `.gatesrc`. Measured 2026-10-12, both clean (necrohand's 133 tracked `.py`, the metarepo's 13 in
  `tools/`), so opting in turns nothing red; or drop pyflakes for ruff's F rules, one vocabulary.
- **antiknob, divoom-control, routines, ztools, after `51109d6` (4.9):** call
  `$GOH_DIR/gates/goh.sh status-markers` from the repo's gate, fix what is cheap and `--seed` the
  rest. At adoption (2026-10-11): antiknob 113 sites in 9 files (Rust `src/bin/`, `install.sh`,
  `ui/build.sh`), divoom-control 52 in 11, routines 3 in 1, ztools 3 in 1. ZoneWM reads 0 and
  adopts it with no seed.
- **antiknob, after `a74bbff` (4.14):** its Rust `src/bin/` sites can print through `goh-tui`:
  `goh-tui = { path = "../gates_of_heck/crates/goh-tui" }` (Cargo reads no env var in a path, so the
  sibling checkout IS `$GOH_DIR`; its `build.rs` reads the stylerc beside it, never a copy), then
  `goh_tui::outln!("{OK} ...")` in place of each `[ Ok  ]`, and re-seed `status-markers` smaller.
  Re-measured 2026-10-12 by the consolidation: 43 hand-rolled `[ ==> ]` prefixes left in its Rust
  binaries.
- **ztools, after `5c5868e` (6.5):** its next release through the kit refuses while its CI on
  HEAD is red (red since 2026-10-10: ci.yml reads the gitignored `.python-version`). Fix the CI;
  `--no-ci-check` exists, says so aloud, and is not the fix. sys_updater has no workflow and
  releases with "no CI" stated.
- **divoom-control, monitor, antiknob, CadGoose and ztools, after `bfac28b` (6.8):** each pushes
  its homebrew-tap bump itself (`scripts/release.sh`, `release.sh`, `release.sh`,
  `scripts/push_release.sh`, `tools/release.sh` with `tools/upgrade_tap.py`), so the tap's gate
  never runs on it. Replace that step with `$GOH_DIR/tools/release-kit/tap_bump.sh --tap
  ztomer/homebrew-tap --formula|--cask NAME --tag vX.Y.Z --artifact PATH|URL`.
- **ztools, after `3103441`:** source `$GOH_DIR/lib/gpu_lock/gpu_lock.sh` and delete
  `tools/gpu_lock.sh` (its header cites a desktop-lock path that is gone); point
  `rust/tests/gpu_lock_shell_parity.rs` and `tools/tests/test_gpu_lock_shell.py` at the house
  file, so the Rust client's parity is judged against the one bash half. Hooks reinstalled
  2026-10-10 (`79698b2`).
- **scripts:** `lib/test_desktop_lock.sh` (15110 bytes) is a second, drifted test of this repo's
  `lib/desktop_lock/` (whose own is 6472): fold what it covers that ours does not into ours, then
  delete it.
- **After the Python 3.15 move (2026-10-10, Antigravity `460d804a`):** divoom-control
  `.github/workflows/tests.yml:45,111` and antiknob `.github/workflows/tests.yml:75` still pin
  3.14 for jobs that run this repo's gates; scripts `tests/test_proc.py:211` looks for coverage
  `.pth` files only in `site.getsitepackages()`, so the user-site `a1_coverage.pth` is missed
  (266 vs 267); necrohand's 5 spaCy modules run only on 3.14; Finance/zinc fails on 3.15
  (PyMuPDF imports `typing.ByteString`, and the repo makes warnings errors).
- **Over the line cap since build files entered scope:** CadGoose [`e1fa2b3c`] `CMakeLists.txt`
  (702) and games/CadGoose2 [`a465180`] `CMakeLists.txt` (1142, re-measured 2026-10-09; it aborts
  layers 2-3 under `gate.sh --full`'s `set -e`). No exclude names them; split, do not exempt.
- **Callers of a retired Python checker by path** (forwarders keep working): ZoneWM [`d2aa402b`]
  `tools/release_precheck.sh:35`; app_updates [`22ec4aa2`] `tools/gate.sh:35,51`; koffee_big
  [`9e375e37`] `tools/gates_lint.sh:28,30,131`; Finance/salary [`e553bf2f`], Finance/zinc-core
  [`972bcd47`] `tools/local_ci.sh`; CadGoose2 [`1a3f659d`] `.gatesrc:37`; necrohand [`fae673d3`]
  `Makefile:80,254`. Move to `bash "$GOH_DIR/gates/goh.sh" <check>`.
- **ZoneWM:** `.gemini-workspace` points at `docs/GEMINI.md`, `docs/ARCHITECTURE.md` and
  `docs/CONTRIBUTING.md`, none of which exist; its census sittings hold the house desktop
  and GPU locks since `90336053` (D-0260); the single probes still hold only `~/.zonetiler-qa.lock`.
- **ZoneWM** [`d2aa402b`]: `goh requires-call` expresses `check_probe_placement.py` and both halves
  of `check_probe_courtesy.py` as rows; `.gatesrc` sets no `GOH_REQUIRES_CALL` and both copies are
  there -- 5 of its 17 LEGITIMATE entries would go stale as rows. Deleting them is its owner's
  call. `check_focused_border_verdicts.py --probe` failed alone at that HEAD (a fill-arm
  assertion). Its 3.3 row is closed (`c8766158`).
- **monitor** [`b690cfc8`]: CI still runs `GOH_NO_NATIVE=1 ... structural.sh --full` (`ci.yml:67`;
  the key is retired and ignored, `.gatesrc:44`, `ci.yml:119`) and a plain `cargo test --workspace`
  beside the gate's. Its customised hooks are monitor's call.
- **routines** [`a4712bcc`]: `.gatesrc:26` still runs plain `cargo test --all-features --locked`
  (keep `--doc`; P1c landed in v0.21.0).
- **ztools** [`efe364a1`]: `rust/` done; `vendor/camoufox-rs` still runs a plain `cargo test` and
  carries 24 `assert!(..is_empty())` (rust/ 1). `GOH_EXCLUDE='^vendor/'` is set. Not told of
  v0.24.0 or v0.25.0, and 13 undeclared stdin calls (3.3).
- **servers / media_server** [`a1a1d9ac`]: `scripts/dev/check.sh` step 6 runs pytest every push
  (`1548344` cut it 206-239 s -> 51 s at load 56 under xdist, and found the cost was serial
  execution rather than repetition, so a scoped proven cache for the repo tests is no longer
  needed); `tools/gate.sh:88` still loops crates with `xargs -P 4`: adopt `rust_gate.sh
  --each-crate` and `GOH_RUST_LINT_CARGO=cargo-zigbuild` (v0.22.0). 88 undeclared stdin calls
  (3.3).
- **divoom-control** [`0ac21228`]: CI installs with `required_tools.py --layer structural --names`
  plus apt, not `--repo . --install`.
- **Every consumer pushing by branch name over HTTPS:** `push_gate.sh` refuses a ref that moved
  while it was gated; ZoneWM's pinned `git push <remote> <sha>:<branch>` is the airtight form.
- **O35 (servers):** a per-consumer `gate_calibration.json` registry; a feature here, unscoped.
  Re-reported open by the 2026-10-12 consolidation; servers carries a pointer to it.

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
- The ratchet machinery is unit-tested both ways, but neither opted-in repo carries an allowlist
  (both declared every call), so it has never been exercised against real debt.

### `goh unreaped-spawn` + `lib/orphan_canary.py`

- A child that calls `setsid()` leaves the step's process group, the canary's only evidence
  (`ppid == 1` cross-reported every concurrent gate). Needs its own signal, e.g. a helper pidfile.
- Per-function, not interprocedural: a spawn RETURNED is a handoff (four in the estate). Same-file
  callees that can panic are read as panicking; other files and crates are not. Cross-CRATE guard
  types are reported `unjudgeable`, counted and named, never failed on.
- A kill inside a conditional (`if cond { child.kill(); }`) is accepted. Cost unknown; needs a
  corpus. Rust, Python, shell only: no `.swift`/`.ts`/`.go` test spawns in the estate today.

### The C2 writer hook and the commit gate

- `hooks/claude/skill_edit.sh` judges a Bash command that NAMES the corpus; one that writes it
  without naming it (a `cd` there first, a variable) is judged at the next commit or push.
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
