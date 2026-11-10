# BACKLOG — the roadmap: open work only (house rule #16)

One forward-looking file. Completed work lives in git and `CHANGELOG.md`, never here. Every open
entry has an id and a `Done when:` clause naming the test, command or observable that proves it;
`(owner to decide)` where none is known. An entry that is also an umbrella queue item says
`(queue: <ID>)`; the queue (`~/Projects/.loop/queue.json`) holds its brief, and `goh roadmap` refuses
an entry whose queue item is complete. A perf change lands only with a before/after from the P0
instrument; a cache lists every way a hit can be wrong, each with a test BEFORE the cache exists.

Plan ids older than 2026-11-08 (`BACKLOG 6.2`, `4.3`, `5.1`, ... as cited in code comments and the
CHANGELOG) resolve in this file at `1a7c444`: `git show 1a7c444:docs/BACKLOG.md`.

## Rulings in force

- **Main moves only through `tools/land.sh`**, from a worktree; since `7e26571` (2026-10-10) the push
  refuses a main tip land.sh did not gate (`GOH_LANDED_ONLY=main`). Every Rust change runs
  `cargo test --workspace` before its commit.
- **Ownership is a repo, never a session** (2026-10-14). Consumer debt is the consumer's own roadmap's;
  this file keeps what the gate layer here must do. The consumer-notes section of 2026-10-20 was
  pruned on 2026-11-08 for that reason.
- **Build locally; prefer publicly maintained tools and Claude Code's own features** (owner,
  2026-11-08, queue): build only the specialised parts on top.
- **The box is never quiet** (owner, 2026-10-08; load 10-132 seen since), so a figure is a rust step's
  CPU over the rust control, a native step's raw CPU, a count, or a same-run comparison
  (`lib/step_timings.py report`, `tools/gate_profile.sh`), never a quiet wall time. Spawn count,
  tree walks and network round trips are the cost metric, not CPU (16 cores, sys about equal to user).
  A string comparison of a path measures the CALLER, not the filesystem (v0.25.1).
- **The machine is the owner's to use** (2026-10-10), apart from other sessions doing their own work.
- **Consumers are told of a tag: servers and ztools only** (owner, 2026-10-08, for quota).
- **The browser suite's UI timing is not goh's to fix** (measured 2026-10-20 in divoom-control): the
  parallelism (`pytest -n 8`) is the consumer's own; goh runs that suite serially as one step. The fix
  is serial in the local gate as CI runs it, or an assertion that waits on a state, not a timer.
- **`tests/reference_kit.py` runs the retired Python from `96018bd`**; a shallow clone without that
  history cannot run the parity suites and is refused by name. The spec is never re-pinned.
- **Cross-session serialization** (`tools/session_bench.py`): sigma 0.107 for `structural --full` at
  N=1-8 under a hold (`75f109c`, 2026-10-08). Re-run it after any change that adds work to the
  `--full` sweep; above 0.15 it re-opens as an entry here.

## Phase 1 — make the standing rules structural

Why first: each is a rule that only discipline held, and broke on 2026-11-08; none depends on another
phase, and each is one landing.

- **GOH.RUNNERPIN** (queue: GOH.RUNNERPIN) A moving runner label breaks "latest is a named release":
  antiknob, divoom-control and monitor use `*-latest` labels, and CI warns that `ubuntu-latest` moves to
  Ubuntu 26. Add a goh check refusing `*-latest` `runs-on` labels and moving action tags in
  `.github/workflows`, then pin those three repos to named images.
  **Done when:** the check is red on a fixture with `ubuntu-latest` and green on a named image; antiknob,
  divoom-control and monitor are pinned and their CI is green.
- **GOH.ROADMAPREQ** (queue: GOH.ROADMAPREQ) A repo with no `GOH_ROADMAP` still passes with a warning.
  Seed the remaining repos (koffee_big, ZoneWM including retiring `check_stale_backlog.py`,
  divoom-control, monitor, scripts, servers/media_server including its stale History sentence,
  a private nested workspace, the 8 peer repos), then turn the warning red.
  **Done when:** every gated repo declares `GOH_ROADMAP` (a path or `none`); an unset one is red, proven
  on a fixture.
- **LOOP.INSTALLED2** (queue: LOOP.INSTALLED2) The goh part: `GOH_INSTALLS` is a `.gatesrc` key used by 8
  repos but absent from `docs/config.md` and from any goh check. The other parts (the three
  `installs.HELD` rows, the pushes in four repos) are `.loop`'s and the queue's.
  **Done when:** `GOH_INSTALLS` is documented in `docs/config.md` and checked in goh, test first, landed
  via `tools/land.sh`.

## Phase 3 — public CI

Why third: the hosted run should judge the pinned runner images Phase 1 introduces, and the repos are
public (owner, 2026-11-08: "Yes, public repos").

- **CI.PUBLIC2** (queue: CI.PUBLIC2) gates_of_heck and monitor (multitop) still lack a GitHub-hosted run
  of their own gate (they were held). divoom-control's CI runs its checks as separate steps instead of
  `tools/gate.sh --full`, so CI and the local gate can drift (one gate, one command: CI execs the gate),
  and its `tests.yml` carries stale comments that gates_of_heck is private plus a
  `GOH_TOKEN || github.token` fallback it no longer needs. Pin actions and goh to released tags; confirm
  each repo is PUBLIC before leaving Actions on.
  **Done when:** gates_of_heck and monitor have a green hosted run of their own gate on main HEAD; divoom
  CI execs `tools/gate.sh --full` with the stale comments and the token fallback gone.

## Phase 5 — speed of the checks

Why last: nothing else waits on it, and the rework of the gate rules above changes what is worth timing.

- **RS.GOH** (queue: RS.GOH) Port the high-frequency text-scanning and AST checkers in `checks/` and
  `gates/` (file length, conflict markers, emoji, early-exit pipes, secrets, dead-after-exec, bare python
  hooks) to native Rust (rayon, ignore, regex). The checkers left in Python (`check_baseline_ratchet`,
  `check_display_seam`, `check_generated_fresh`, `check_tests_registered`, `check_swift_*`) are outside
  this item's list; a consumer runs each with arguments and no structural step runs them.
  **Done when:** the named checkers run as one Rust binary with the same verdicts on goh's checker tests
  and every consumer repo, a pre-commit run measures under 100 ms, and the Python versions are deleted.
- **GOH.MSPUSH1** A media_server push with one crate changed, warm: 197 s baseline, 183 s at load 7-12
  (2026-10-06), of which 110 s was media_server's own pytest suite, which its `gate.sh` runs outside the
  proven cache. media_server `15483446` cut that step from 239 s to 51 s at load 56 (xdist); the push has
  not been re-measured since. The old target was 30 s, unreachable while that step runs unconditionally.
  **Done when:** (owner to decide) whether 30 s stays the target; re-measure the push first with
  `tools/gate_profile.sh` read by the CPU-over-control rule.
- **O35** (servers) A per-consumer `gate_calibration.json` registry; a feature here, unscoped,
  re-reported by the 2026-10-12 consolidation.
  **Done when:** (owner to decide)

## Known limits, stated so they are not rediscovered

### The stdin gate

- **Python only.** No shell, no Rust, no Swift, no JS/TS, and no Python embedded in a shell heredoc (two
  such sites known 2026-10: CadGoose2 `tools/ab_goose_overlay_fps.sh:33`, `scripts/package_macos.sh:199`).
- **One level of attribute.** `runner = subprocess.run; runner([...])` is invisible, by design
  (`crates/goh/src/substdin/calls.rs`): reaching through an unresolved attribute is how a port invents
  findings nobody has to fix. `stdin=None` is legal ("inherit, on purpose").
- **A Python 2 file is an error, not a skip** (measured on a third-party clone): right as a policy,
  wrong as a dialect for the scanner.
- The scope is `git ls-files --cached --others --exclude-standard`: tracked plus untracked-not-ignored.

### `goh unreaped-spawn` and `lib/orphan_canary.py`

- A child that calls `setsid()` leaves the step's process group, the canary's only evidence (`ppid == 1`
  cross-reported every concurrent gate). It needs its own signal, e.g. a helper pidfile.
- Per-function, not interprocedural: a spawn RETURNED is a handoff. Same-file callees that can panic are
  read as panicking; other files and crates are not. Cross-crate guard types are reported `unjudgeable`,
  counted and named, never failed on.
- A kill inside a conditional (`if cond { child.kill(); }`) is accepted. Cost unknown; needs a corpus.
  Rust, Python and shell only: no `.swift`/`.ts`/`.go` test spawns in the estate.

### `goh moving-tags`

- **Shell and Makefiles are not read** (decision, `movingtag/mod.rs`): a flag grammar where `-v a:b` has
  the shape of `name:tag`, references in variables, built names that look pulled. Un-policed floats at
  v0.31.0: monitor `build.sh:96` (`rust:alpine`) and `tools/lint_linux.sh:19` (`rust:1-bookworm`);
  media_server `scripts/dev/linux_test.sh:21` (`rust:1`).
- **A version-shaped tag is not judged for coarseness**: `alpine:3`, `postgres:18-alpine`, `recyclarr:8`
  float across rebuilds and pass. A per-repo opt-in demanding a digest everywhere is the way, if wanted.
- **A Helm values `repository:`/`tag:` pair is not joined**, and a templated `image:` is counted
  unreadable (apes: 13 of 25 references).

### The C2 writer hook and the commit gate

- `hooks/claude/skill_edit.sh` judges a Bash command that NAMES the corpus; one that writes it without
  naming it (a `cd` there first, a variable) is judged at the next commit or push.
- The commit gate is the STAGED structural layer by design; the suite runs at push.

### SUPERSOTA residuals (`docs/SUPERSOTA.md` §3)

- R3: `check_estate_corpus.py` proves a checker still REFUSES a plant; it cannot prove the checker is
  correct, and consumer-side checks are unmeasured.

## Deferred, each with its re-open condition

- Secrets v2 (entropy): measure the false-positive rate on all consumer trees before enforcing.
- shfmt: only as reformat-everything, or a flag set proven clean on this tree.
- Swift coverage merge: needs a real-swift oracle across Xcode versions.
- `secret-ok` scope: line plus line-above; path-scoped rules only under real fixture pressure.
- `gates/lcov_merge.py` native port: re-open when a merge measures over 1 s (largest 2026-10: 76 ms).
- The `--full` sweeps (`check_empty_scope.py`, `check_probes_pass.py`) stay Python by SUBJECT: they run a
  repo's own Python gates. Re-open when either costs over 1 s on a consumer's push.
- A native `goh step` (the ceiling wrapper in Rust, about 3 ms against 26 ms): `nohup`'s contract (a
  signal ignored at entry stays ignored) needs the inherited disposition, which only `sigaction` reads,
  and `unsafe` is denied in the crate. Re-open with a vetted crate that exposes the query safely, or a
  per-site exemption the unsafe allowlist names.
- One `rev-parse --show-toplevel` per delegated Python checker process (two per `--full`): a
  cross-process answer was judged not worth its staleness risk. Re-open if a shim count shows more.
- Credentials: `.netrc` is out of scope until some gate already reads outside its repo; CI configs are
  committed, so `goh secrets` covers them.
- A seam for a gate whose evidence lives in ANOTHER repo: the export now links every sibling a tracked
  manifest names (`cf51846`, `fb3d0a7`), but `GOH_CROSS_REPO_ROOT` is deliberately not a symlink into the
  worktree, so ZeroThunder invented a per-repo `GAF_DIR`, which RAISES when absent ("no checkout" must
  never read as "uncalibrated"). One house pattern, once a second repo needs it.
- The `git archive` export has no `.git`, so `structural.sh` cannot judge one (it reads the index). A
  clone is the faithful measurement; re-open only if an export ever has to be gated.
- Deleting `lib/mcp_scaffold.py` (2026-10-31): ZeroThunder's `tools/zerothunder_mcp_server.py` still
  imports it. Delete only after ZeroThunder moves to `app-mcp-bridge` (the `rust-mcp-rmcp` skill's bridge
  pattern), and re-run the user grep over every git work tree under `~/Projects` at any depth first.
