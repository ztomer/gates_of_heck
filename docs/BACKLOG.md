# BACKLOG — the roadmap, and every open item (rule #16: one forward-looking file)

One file; prune landed items to git history. Every item carries the same four things, or it is
not ready to start: the **measured baseline** it moves, the **exit number** that closes it, the
**red-first test** that proves it can fail, and the **ways it can lie** (for a cache: every way a
hit can be wrong, each with a test BEFORE the cache exists). A perf change without a before/after
from the P0 instrument does not land.

## State — 2026-10-20, v0.44.0 (read first)

v0.49.1 (2026-10-25) is fixes only: the load registry's lock, the backstop surviving slow lookups, the py runner decided by tracked state; the CHANGELOG stanza has each.

v0.49.0 (2026-10-25) carries the estate roadmap's goh lane (G1c, G1e, G1b(a)(b), R3.13 two of three); the CHANGELOG stanza has each with its commit. G1b(c) and R3.13's sys_updater pin are ruled not goh's there, with the evidence.

v0.44.0 closes the plan of record's Phase 3 (7.1, 7.2, 7.3): the Linux `pidfd` branch of
the gate slot holder is PROVED on Linux (and three defects it carried are closed), the
proven cache honours the ARTIFACTS a step declares, and the empty-tree sweep collects the
SHELL gates. The CHANGELOG stanza carries each in full.


v0.31.0 adds `goh moving-tags` (no container image on a moving tag; CHANGELOG), Adv360-Pro-ZMK
P0.2c. v0.30.0 added the Claude Code discard guard.
v0.29.2 closes 5.1: media_server's push gate is 83 s (was 122 s), because rust coverage keeps its
instrumented build outside the push worktree. v0.29.1 is one fix, found by landing v0.29.0: a gate takes the binary built from the commit it
pinned (a stamp-keyed store, `bin/by-stamp/`), so a goh land no longer turns every consumer push in
flight red with a false "BEHIND". v0.29.0 adds `goh tap-writes` (no direct write to a Homebrew tap; CHANGELOG), and is the first tag
to carry v0.28.1's fix, which was written up but never tagged. v0.28.1 (2026-10-15) is one fix, found from the outside: the Swift coverage gate could not read
an xcresult at all, so no Swift repo could set a floor (`koffee_oss` asked for one since
2026-10-14). The CHANGELOG stanza carries it in full.

**Reported from a consumer, per a roadmap item that said to report rather than paper over:**
`koffee_oss` Phase 1 G2 asked for tracked Xcode build output to be removed and then, in the
absence of a house check, for the gap to be reported here. Six files under `DerivedDataTests/`
had come in with the history flatten. They are untracked and ignored on that side now. The check
is **CLOSED** at v0.44.0 — `checks/check_tracked_build_output.py` walks `git ls-files` for the
build-output shapes (`.o`, `.dSYM`, `DerivedData`, `target/`, `__pycache__`, `node_modules`,
`*.xcactivitylog`, and the bundles) and names every tracked file that is one; a repository that
tracks nothing is REFUSED rather than passed. It is in `checks/`, not in the structural layer,
and the reason is measured: over the estate on 2026-10-20 **one repo of twenty-two** (the
repositories with a `.git` under `~/Projects`; `old_phones`, 3 files) carries a tracked build
output, so a HARD structural gate would land red in that
consumer's next push — and a gate that lands red in a consumer needs that consumer's seed, which
is a write in another repository. Making it structural, with `old_phones`' seed, is the OWNER'S
call (see 7.4).

**And a defect in this repo's own PUSH path, found while landing it — CLOSED 2026-10-15.**

The pre-push gate exports HEAD and runs the suite in the export. `bin/goh` is **gitignored**, so
the export never contains one, and the tests that exercise a push then fail trying to build one:
`built goh outside the session fixture (cargo build …): pass the session binary
(GOH_BIN=native_goh_path())`. Reproduced with no push involved at all. Re-measured 2026-10-15 in a
bare export of HEAD: **30 failed / 14 passed** (the 26 first recorded predates the suite's growth);
adding `bin/goh` back made all **44 pass**. So the variable is the gitignored binary, not
parallelism, not load, and not any commit's content.

Consequence, and it is the part that matters: **`tools/gate_profile.sh` passing in a checkout that
has `bin/goh` says nothing about whether the pre-push gate will pass**, because the push gate
judges the same tree in a place that cannot have one. A gate whose verdict depends on where it
runs.

**RULED and fixed:** the export seeds `bin/goh` from the caller's build (the first of the two
options), through the mechanism that already exists for exactly this — `GOH_EXPORT_KEEP` in this
repo's own `.gatesrc`, which copies the named ignored files into the worktree. One line; no code
path added. It is scoped to THIS repo on purpose: only here is the export also `GOH_LIVE_ROOT`,
which is where `_goh_bin.sh` resolves the binary. A consumer's export resolves the binary from the
live gates checkout, never from itself, so every consumer's `.gatesrc` stays empty.

Proved by `tests/test_push_gate.py::test_this_checkouts_export_carries_bin_goh` — red first
(`GOH_EXPORT_KEEP` read empty), red again on removing the key, green after. A push here no longer
needs `--no-verify`; the v0.28.1 push was the last one that did.


v0.28.0 (2026-10-14) closed 6.15-6.19 and 6.21-6.22 — **6.20 is still open**, see its row: the stock hooks state what they do rather than what a repo lacks (6.19), `structural` grew `--keep-going` (6.16), `emoji` reads GOH_ALLOW from .gatesrc on its own (6.17), `lock-version` reads manifests as TOML (6.18), a tap bump moves version, urls and digests together (6.15), every checker is in the calibration registry (6.21), and a producer that never ends gets a bounded wait instead of a capture (6.22). The stanza carries each in full; the commits are in git history. The one consumer-visible default: `goh structural` now stops at its first red unless `--keep-going` is passed, so a repo scripting that step needs the flag if it wants every red.

v0.27.0 (2026-10-14) closed the v0.27.0 roadmap's correctness phase (4.3 every scanner declares
its subject, 4.6 a render that counts frames must deliver them), flipped the stdin gate on by
default (3.4), and closed the first measurement (5.3). What landed, with its commit and what was
wrong before, is the CHANGELOG stanza; the landed items themselves are in git history (this file
at `ab0c4dc`). Box: 16 cores, 4-5 busy at idle, sys ~= user, so **spawn count, tree walks and
network round trips are the cost metric, not CPU**. The box is never quiet for the foreseeable
future (the owner, 2026-10-08; load 10-132 seen since), so a figure is read by 5.3's rule: a rust
step's CPU over the rust control, a native step's raw CPU, a count, or a same-run comparison,
never a quiet wall time. One lesson from v0.25.1 still governs any measurement work: **on this
box, a string comparison of a path is a measurement of the CALLER, not of the filesystem**.

## Resume here (2026-10-14)

- **Nothing in this repo is ready to start.** 5.1 was met on 2026-10-17 (v0.29.2, 83 s); 3.3's rows are each owned by the repo they sit in,
  which clears them in its own Phase 0. **Ownership is a repo, never a session** — a session is
  deleted and takes the pointer with it; the repo's roadmap is what survives. A new item enters
  through the plan of record below with its four fields.
- **Waiting on the owner, and nothing here moves them:**
  - 6.13's scheduling: `scripts/estate_audit.py --install` writes and loads the daily launchd job.
  - Registering `hooks/claude/estate_advisories.py` (SessionStart) in `~/.claude/settings.json`.
  - `GOH_RED_RUN` turned on, per repo: no repo has it on yet (`e526df9`).
- **Main moves only through `tools/land.sh`**, and since `7e26571` this repo's push refuses a
  `main` tip land.sh did not gate (`GOH_LANDED_ONLY=main`). Every Rust change runs
  `cargo test --workspace` before its commit, and the suite runs before a commit to gate or test
  code.
- **The machine is the owner's to use** (2026-10-10), apart from other sessions doing their own
  work: a measurement may hold the desktop and the GPU for as long as it needs.
- Consumers told of a tag: servers and ztools only (the owner's call, 2026-10-08, for quota).
- Owed from 2026-10-08, unchanged: servers' `push-gate-build-dir-config` verified against servers
  (its content is here at `d98d7bd`).

## Roadmap to v0.43.0 — the plan of record (re-phased 2026-10-20)

Item ids are kept from earlier plans so commit messages that cite them still resolve. Every item
carries **Baseline:**, **Exit:**, **Red-first:** and **Lies:**, and a done item names its commit,
or `tests/test_backlog_items.py` fails; a landed item is pruned to the CHANGELOG. `[ ]` open,
`[x]` done, `[~]` handed off.

### Phase 1 — the measurements, each waiting on something outside this repo

5.3's rule reads every figure here: a rust step's CPU over the rust control, a native step's raw
CPU (`lib/step_timings.py report` prints both; `tools/gate_profile.sh` runs the controls). The
other instruments: P0 (`GOH_TIMINGS`), `cpu_ms` (`e3e5418`), `tools/quiet.sh` and
`lib/bench_hold.py` for holds, and `tools/session_bench.py` for cross-session serialization.

- [x] 5.1 media_server push, everything changed, warm -- **MET 2026-10-17 (v0.29.2): 83 s**, control
      drift +2.7% (valid by 5.3), load ~42, against 122 s on the same commit an hour before. The
      instrumented build lived in `$PROJ/target/llvm-cov`, inside the push worktree that is deleted
      after every push, so every crate's coverage built its dependencies cold on every push while
      clippy (cargo's own build dir, keyed on the same stable worktree path) stayed warm. The build
      dir now lives in `$GOH_COV_CACHE/<path key>/build`; coverage CPU across the 28 crates 393 s ->
      216 s (112 -> 68 control units). The first push after a cache is empty pays the old cost once
      (109 s measured). The media_server reds this item waited on cleared at `fce7d32`. Next
      candidates, if 90 s ever slips: the 28 cross clippy runs (72 s wall, 42 s CPU -- contention,
      not work) and coverage's sys time (test spawns).

### Phase 2 — the estate is every consumer, not one directory

CLOSED at v0.42.0: 6.14, the estate is every repo `install.sh` installed into. The registry is
beside `estate_audit.json` (`installed_repos`, one line per toplevel), `install.sh` writes it,
`estate_audit.py` and `scripts/agent_audit.py` read it UNIONED with `GOH_ESTATE_ROOT`, and a
registered path that is gone is named. The CHANGELOG stanza carries it in full; the commit is in
git history.

6.20 and 6.19 are among the eight v0.28.0 closed above; 6.20's fix is `install.sh`'s refusal
split by what may refuse — a locally modified hook is refused ALONE (named, left byte-for-byte,
every other hook still installed, exit non-zero) while another manager owning the hooks still
refuses the whole run and writes nothing. Its two cases in `tests/test_install_atomic.py` were
seen red against the pre-fix script.

### Handed off — each row is its consumer's

The stdin gate is on everywhere since `0c93f4d`; the sweep's method is a mechanical AST pass
placing `stdin=subprocess.DEVNULL` at each call's closing paren from the `--source` JSON's line,
then `ruff format` (goh's 555 calls), or `--seed` (`498eb9a`) for what a repo cannot declare yet.

- [ ] Adv360-Pro-ZMK P1.3 (the CONSUMER's step, not this repo's): the base-pin row is blocked on
      ITS OWN gate being able to express a commit pin. The house can now NAME one — `goh
      moving-tags` judges a commit id as its own verdict, beside the digest (v0.47.0), because a
      gate that cannot EXPRESS a pin cannot accept one — but the consumer's `tools/check_zmk_base.py`
      still REFUSES a sha revision: `problems()` reads the Zephyr line out of the revision NAME
      (`adv360-z3.5-2`), so a sha is "a revision that names no Zephyr line" and the check goes red
      BEFORE `report()`'s commit-pin branch can run. That branch is DEAD CODE today, and it
      contradicts the docstring beside it. The change belongs in the consumer's repo: accept a sha
      revision, and read the line from the resolved manifest. Baseline: MEASURED 2026-10-20 in the
      consumer's repo (its ROADMAP P1.3 carries the numbers). Exit: the consumer's gate is green on
      a sha pin and red on a mismatch. Red-first: the consumer's `tools/check_probe_bite.py` proves
      each shape. Policy: the house does NOT rule sha-or-branch (a commit pin cannot move, so a
      build is reproducible, but it does not follow its upstream; a branch follows its upstream and
      can move under the build) — the OWNER rules, and the gate only has to be able to say which
      one it is. Lies: a revision NAME that merely looks like a sha (a branch called `d93499bb`)
      passes a name-based test and names no commit; a sha no repository has is a pin of nothing;
      a line read from the WORKING tree instead of from the pinned commit names a Zephyr the build
      never used; and a resolution that fails must be NAMED, never a silent green.

- [~] 3.3 The estate sweep, handed off: since 3.4 (`0c93f4d`) each remaining row is red in its own
      repo and cleared by that repo, in its own Phase 0 (Downstream). Each row below names the
      repo that owns it, so the sweep survives the deletion of any session. Baseline: MEASURED 2026-10-09 with this tree's own binary, scope
      `git ls-files --cached --others --exclude-standard`: 34 repos carry Python, 1361 process
      calls, **996 findings**, 17 repos exit 1. Three rows are closed: ~/.claude/skills (outside this table's scope, 6.14; 35 calls declared 2026-10-14, claude-skills `9618499`, floor 35 proved red at 36), gates_of_heck
      (511 then, 555 calls in 186 files when declared, `df8f9c2`: no allowlist, 614 calls read,
      0 findings, floor 550 proved red at 10000) and ZoneWM (118 then, its `c8766158`: 123
      declared, floor 110 of 125). **12 rows remain, 232 findings as of 2026-10-09** (re-read each
      before its round): Finance/zinc 71, divoom-control 52, scripts 52,
      koffee_big 23, ztools 13, games 6, app_updates 5, old_phones 4,
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

### Phase 3 — four reports from the consumers (2026-10-20)

Each item was reported from a consumer's own roadmap and carried the four fields before it was
started. None of them is a consumer's to fix: the class is in the gate layer, and the layer is
here.

- [x] 7.1 (`ee9a510`) The Linux `pidfd` branch of the gate slot holder (v0.39.0) had never run. **Baseline:**
      v0.39.0 shipped `lib/gate_slot.py`'s owner watch -- kqueue NOTE_EXIT on macOS, a pidfd on
      Linux -- and the pidfd branch had NEVER been taken: this repo's suite is macOS (`select`
      has `kqueue` there), no consumer ran the Linux path, and nothing said so. **Exit:** the
      branch is PROVED on Linux (a container run reports the pidfd watch and not the pid poll),
      and the three defects it carried are closed. **Red-first:**
      `tests/test_gate_slot_linux.py` -- 4 cases red before the change, and the 5 Linux-only ones
      green in the container (`docker run ... pytest tests/test_gate_slot_linux.py`: 7 passed).
      **Lies:** (a) a kernel WITHOUT pidfd (Linux before 5.3) answers ENOSYS, an OSError -- not
      the ProcessLookupError that means "the owner is already gone"; unhandled it killed the
      holder, and a holder that dies FREES the slot, so the machine-wide limit silently stopped
      limiting; (b) select(2) refuses a descriptor at or above FD_SETSIZE (1024) and a pidfd is a
      plain descriptor, so the watch died where the fallback was never reached; (c) the owner can
      be pid 1 -- in a container the consumer's `tools/gate.sh` IS the container's init process --
      and the ancestry walk stopped BEFORE 1, so the holder refused the watch and the consumer's
      own gate.sh FAILED with "the slot holder exited without a slot". A pid-1 record must read
      as "nested", or a nested gate waits for a slot its own parent holds.

- [x] 7.2 (`1c2f9a6`) The proven cache could skip a BUILD whose artifact a later step consumes (divoom-control,
      2026-10-20). **Baseline:** the pre-push gate runs in a FRESH worktree; the build step had
      been proven on the same commit by an earlier push attempt, so it was SKIPPED, so it left no
      binary behind, and the consuming step failed with "divoomd (Rust daemon) binary not found"
      -- a reason that has nothing to do with the tree. The cache read "the step exited 0" as
      "the artifact a later step needs is there". **Exit:** a step DECLARES what it produces and
      consumes (`[produces=...]`/`[consumes=...]` in the step string, `gates/_steps.sh` parses it),
      a record is written only when every declared artifact is THERE, and a hit is only a hit
      while every declared artifact is still there, unchanged -- the bash half and the native
      half (`goh proven`) agreeing byte for byte. **Red-first:**
      `tests/test_proven_artifacts.py` (9 cases, all red before the change) and the two new
      cases in `tests/test_proven_native.py`. **Lies:** (a) a build that "succeeded" and produced
      no artifact -- the step is RED and nothing is recorded; (b) a stamp that matches a STALE
      artifact -- the BYTES are what the proof names, so a changed artifact voids the hit; (c) a
      step that consumes a build from another commit -- the consumed artifact's bytes changed, so
      the consumer's proof is void.

- [x] 7.3 (`24f5214`) The empty-tree sweep collected only `check_*.py`, so a `check_*.sh` gate was never swept
      over an empty tree (Adv360-Pro-ZMK). **Baseline:** the sweep ran every PYTHON gate over a
      skeleton with no content and failed any that reported compliance; the shell half was never
      collected, so Adv360's `check_keymap_build.sh` and `check_upstream_drift.sh` -- both with
      probes -- were never read, and a shell gate could pass over an empty subject with nothing in
      the estate to say so. **Exit:** the sweep collects `check_*.sh` too, runs a shell gate with
      bash, reads the shell's own usage shape (exit 2 and a usage line) as "never looked" rather
      than as a healthy refusal, and skips the shell FORWARDERS by name (a Phase N3 forwarder
      execs the dispatcher; swept as a gate it fails for want of it). **Red-first:** the four new
      cases in `tests/test_gate_runtime_path.py` (all red before the change) and the two shell
      cases in `checks/_empty_scope_probe.py`, which CALIBRATE it on a scratch repo: a shell gate
      that passes over nothing is caught, and one with the guard is not. **Lies:** (a) a shell
      gate that PASSES over an empty tree -- caught, the sweep is red; (b) a shell forwarder --
      named and skipped, never swept; (c) a shell gate that demands an argument -- reported as
      never having looked and excused in `checks/empty_scope_allow.json` with the reason (this
      repo's own `check_no_screen_linkage.sh` audits a BUILT test binary, which an empty tree has
      none of).

- [ ] 7.4 The "no tracked build output" check (koffee_oss, 2026-10-19) -- the CHECK landed at
      v0.44.0; what is left is the DECISION about where it runs. **Baseline:** the ask was in the
      State section above; the estate measurement on 2026-10-20: **1 repo of 22** carries a
      tracked build output (`old_phones`, 3 files), so a hard structural step lands red in that
      consumer's next push. **Exit:** the owner decides (a) the check stays in `checks/` and every
      consumer adopts it in its own gate, or (b) it becomes a structural step with
      `old_phones`' seed file -- a write in another repo, which is the owner's to make. **Red-first:**
      `tests/test_check_tracked_build_output.py` (6 cases, red before the checker) and the canary
      case. **Lies:** (a) a build output that is only ON DISK is `.gitignore`'s business, not this
      gate's -- the tracked-ignored step owns that half; (b) a repository that tracks NOTHING is
      refused by the gate, never passed; (c) a FILE named `target.md` is a document and not a build
      directory -- the shape is a path SEGMENT.


## Open -- measurements (wall times, taken under holds; 5.3 restates them)

A measurement, not a change: the instrument is P0 (`GOH_TIMINGS`, `tools/gate_profile.sh`). The
exit number is the target column; a miss names its lever before any change lands. The met rows
(this repo's suite <= 60 s, a consumer's pre-commit structural layer <= 0.4 s) are in the
CHANGELOG.

| what | baseline | last | target |
|---|---|---|---|
| media_server push, one crate changed, warm | 197 s | 183 s at load 7-12 (2026-10-06): **110 s is media_server's own pytest suite** (134 tests, run by its gate.sh outside the proven cache); 5 of 29 crates re-gated -- 3 correctly (path users), healthcheck-rs on the whole tree by design (its tests read the repo root), vpn-watchdog-rs never recorded (fixed in `d384c0d`) | <= 30 s: unreachable while the pytest step runs unconditionally; servers `1548344` cut that step to 51 s at load 56 (xdist), not yet re-measured as a push |
| media_server push, everything changed, warm | 177 s (P0) | 208 s under a hold (2026-10-08, after `06afca4`) | <= 90 s: 5.1 |

The P2 budget curve (`GOH_CI_JOBS` 1/2/4, this repo and routines) is MET and moved into the
CHANGELOG with v0.42.0: **210 / 176 / 158 s** here at `98848cf` and **77 / 51 / 46 s** in routines
at `c9a893f` (jobs 1/2/4, warm, 2026-10-16, `GOH_PROVEN=0` so no step could be skipped; two
sweeps agreeing within 3 s at 2 and 4), against a 186/147/138 s baseline (busy; knee read at j2
in both, 2026-10-08). routines also measured COLD at 150 / 51 / 46 s: a first push pays one-time
builds (`make test` 44 s cold against 3 s warm). `GOH_CI_JOBS=2` is set in each repo's
`.gatesrc` -- j2 takes 65% of the saving here and 84% there.

## Watch — cross-session serialization (`tools/session_bench.py`)

Closed at sigma 0.107 for `structural --full` at N=1-8 under a hold (`75f109c`, 2026-10-08; it was
0.51). Re-run it after any change that adds work to the `--full` sweep; above 0.15 re-opens it here.

## Downstream: what each consumer session needs to know

Verified read-only at each repo's committed HEAD on 2026-10-08 (the sha in brackets) unless a
later date is given; an item marked fixed from a session's word is not pruned. Done and pruned:
antiknob's vendored `_gitutil.py`, antiknob's and divoom's `lock_guard.sh`, antiknob's CI install,
ztools' exempt key, Finance's `"$root.out"`, ZeroThunder's dropped process handle, the starter
`.gatesrc`'s tilde-path comment (`c8e8b4b`, edited in every repo that carried it, 2026-10-12).

- **Corrections from the 2026-10-14 roadmap pass (measured in each repo):** Finance/zinc runs no
  goh structural layer (the pre-commit framework owns its hooks; `install.sh` refuses), so it is
  UNGATED, not "red at its next commit"; wiring it reds stdin 76 of 81, 1 early-exit pipe, 3 md
  links, 2 unreaped spawns, 3 empty-scope, 2 lockver (6.18), 12 emoji in `tests/fixtures/`. Its
  python 3.15 run is green (1115 passed): the PyMuPDF note below is stale. Finance/salary and
  zinc-core are ARCHIVED on GitHub: their rows are not debt anyone will clear; ZincTender was
  removed from the estate by the owner (2026-10-14).
  ZeroThunder carries 9 early-exit pipes in 6 files (not in 6.9's list); its `local_env_vars()`
  prescription is already done and `/private/tmp/zt-gold` is gone. rustls 0.23.45 went red
  in EVERY Rust consumer on 2026-10-14 (RUSTSEC-2026-0346..0349; divoom-control, ztools and
  routines among them): `cargo update -p rustls` (0.23.46) in each.
  CadGoose's `CMakeLists.txt` is 295 lines since its `b377d79` (the line-cap bullet is stale), and
  its tap push is the "Update Homebrew Cask in Tap" step of `build_and_release.yml`, not
  `scripts/push_release.sh`. apes carries 8 undeclared stdin calls that 3.3's table never
  counted (6.14's class). The pre-push gates a CLEAN export, so a gate that reads generated or
  ignored output finds none: koffee_oss's `Koffee.xcodeproj` (xcodegen) and ZeroThunder's `bin/`
  went red or skipped at the reinstall; each repo's gate must build what it reads.
  Adv360's ruling R3 keeps `.githooks/.goh-installed/` uncommitted, against the reinstall note
  below: a standing exception there.
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
- **media_server, read 2026-10-14 at `e1321b9`, red at its next push on two counts:** the
  stock hooks (the reinstall above) and `cargo audit` in
  `crates/yaml-mini-rs`, which denies RUSTSEC-2024-0449 (`unsafe-libyaml` 0.2.11 unmaintained,
  2024-03-25): replace the YAML dependency or record the advisory in the ignore ratchet with its
  reason. 5.1 waits on both.
- **The 12 repos left in 3.3's table, red at their next commit since `0c93f4d` (3.4)**, one
  session each: declare or `--seed`, set `GOH_SUBPROCESS_STDIN_MIN_CALLS` (a repo that must defer
  writes `GOH_SUBPROCESS_STDIN=off`, which is an opt-out in its own file, not a default). A
  declaration is mechanical where no call needs the caller's stdin (the method is in the
  roadmap's Handed off preamble; ZoneWM's `tools/proc.py` is a helper for the plain calls).
- **ZeroThunder, after 4.7:** wrap the SwiftLint step in `GOH_CI_STEPS` as
  `python3 $GOH_DIR/gates/path_invariance.py -- <the swiftlint command>`; it clones HEAD and runs it
  from `/private/var/...`, `/var/...` and `/System/Volumes/Data/...`, so the `relativeDisplayPath`
  baseline that read 1034 violations as new from one spelling reds there, not in a scratch run.
- **ZeroThunder, the D1 goldens work (2026-10-09):** 7 modified files and 1 new
  (`Core/AppDelegate/GoldenFrameDelivery.swift`) are UNCOMMITTED in its tree, plus a worktree at
  `/private/tmp/zt-gold`; break-probes 1 and 2 red in HeadlessTests, break 3 (the guard removed
  from the loop) caught by no unit test, and the machine was in the lid-closed arm, so its 15/15
  says nothing about display independence. ZeroThunder's to land or drop; 4.6 is the house half.
- **Seven repos, after `496b6a5` (6.9), red at their next push until they act:** koffee_big (29),
  scripts (12), koffee_oss (11), apes (7), CadGoose (7), remote_dotfiles (3), ztools (1), counted
  2026-10-12. Fix each
  (`out="$(producer)"; grep -q PAT <<< "$out"`, the shape every finding prints), or adopt what is
  left with `goh early-exit-pipe --seed` and commit `early_exit_pipe_seed.json`, which may only
  fall. By owner ruling (2026-10-12) they stay red until that repo's own Phase 0 takes them.
- **Five repos, after `1c4be61` (6.2), red at the next push until their hooks and gate run Python
  through the seam:** a bare `python3` in `.githooks/*` or `tools/gate.sh` is refused. Counted
  2026-10-12 (calls): Finance/salary 3, Finance/zinc-core 3, games 2, monitor 2, scripts 1
  (ZoneWM's one is fixed in its own tree). The fix is mechanical:
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
- **sys_updater, after `5c5868e` (6.5):** has no workflow and releases with "no CI" stated.
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
- **monitor** [`b690cfc8`]: CI runs a plain `cargo test --workspace` beside the gate's (the retired
  `GOH_NO_NATIVE` it also carried is gone since its `cc2906a`). Its customised hooks are monitor's
  call.
- **routines** [`a4712bcc`]: `.gatesrc:26` still runs plain `cargo test --all-features --locked`
  (keep `--doc`; P1c landed in v0.21.0). New 2026-10-14: 2 emptiness asserts at
  `tests/cli_dispatch_listing.rs:343` and `:374` (`goh empty-assert` names the shape to write).
- **ztools** [`efe364a1`]: `rust/` done; `vendor/camoufox-rs` still runs a plain `cargo test` and
  carries 24 `assert!(..is_empty())` (rust/ 1). `GOH_EXCLUDE='^vendor/'` is set. Not told of
  v0.24.0 or v0.25.0, and 13 undeclared stdin calls (3.3).
- **servers / media_server** [`a1a1d9ac`]: `scripts/dev/check.sh` step 6 runs pytest every push
  (`1548344` cut it 206-239 s -> 51 s at load 56 under xdist, and found the cost was serial
  execution rather than repetition, so a scoped proven cache for the repo tests is no longer
  needed); `tools/gate.sh:88` still loops crates with `xargs -P 4`: adopt `rust_gate.sh
  --each-crate` and `GOH_RUST_LINT_CARGO=cargo-zigbuild` (v0.22.0).
- **divoom-control** [`0ac21228`]: CI installs with `required_tools.py --layer structural --names`
  plus apt, not `--repo . --install`.
- **Every consumer pushing by branch name over HTTPS:** `push_gate.sh` refuses a ref that moved
  while it was gated; ZoneWM's pinned `git push <remote> <sha>:<branch>` is the airtight form.
- **O35 (servers):** a per-consumer `gate_calibration.json` registry; a feature here, unscoped.
  Re-reported open by the 2026-10-12 consolidation; servers carries a pointer to it.

## Known limits, stated so they are not rediscovered

### The browser suite's UI timing under parallel workers

**The class:** a test whose verdict depends on WALL-CLOCK TIMING, exercised in parallel with other
instances of itself. divoom-control measured it on 2026-10-20: three different UI-timing tests
failed in three runs of the browser suite at 8 workers and at 4 -- the bench drag's
`bounding_box()` came back None, the gallery never overflowed inside its wait, and the gallery tab
was "not stable" for a 2 s click. Each passes alone, and in a fresh worktree.

**RULED: not goh's to fix, and here is the measurement that says so.** goh's layer runs that suite
as ONE step among the consumer's 31, and it runs them SERIALLY: divoom's `.gatesrc` sets no
`GOH_CI_JOBS`, so `gates/local_ci.sh`'s scheduler starts one step at a time, and goh's own Python
layer (`gates/py_gate.sh`) runs `pytest -q` with no `-n` at all. The parallelism is the consumer's
own: `scripts/browser_e2e.sh` runs `pytest -n "$DIVOOM_BROWSER_WORKERS"` (8 by default; CI passes
0, serial, on its 3-core runner). So the fix is the consumer's, and it is already written down
there: push the suite SERIALLY in the local gate the way CI runs it, or make the three assertions
wait on a STATE (the element's state) rather than on a timer.

**What goh contributes, stated so it is not mistaken for the cause:** the LOAD. The gate slot
allows one gate per four cores (four at once on a 16-core box), and each of those gates may run
its own eight browsers -- 32 browsers on one box, and a timing assertion that assumed an idle
machine. That is the house's own lesson (`gates/push_gate.sh`'s header: "a step ceiling measured
on a quiet box is a lie on a box running five cold gates") applied one level down: the ceiling is
bounded, the timing assumption is the consumer's.

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

### `goh moving-tags`

- **Shell and Makefiles are not read** (decision, `movingtag/mod.rs`): a flag grammar where
  `-v a:b` has the shape of `name:tag`, references in variables, built names that look pulled.
  Known un-policed floats at v0.31.0: monitor `build.sh:96` runs `rust:alpine` (a channel) and
  `tools/lint_linux.sh:19` `rust:1-bookworm`; media_server `scripts/dev/linux_test.sh:21` `rust:1`.
- **A version-shaped tag is not judged for coarseness**: `alpine:3`, `postgres:18-alpine` and
  `recyclarr:8` float across rebuilds and pass. Re-open with a per-repo opt-in that demands a
  digest everywhere, if one is wanted.
- **A Helm values `repository:`/`tag:` pair is not joined**, and a templated `image:` is counted
  unreadable (apes: 13 of 25 references).

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
