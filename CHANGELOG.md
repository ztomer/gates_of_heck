# CHANGELOG

## Unreleased

* **`goh dead-after-exec`: no code after an `exec` that replaced the shell** (`crates/goh/src/deadexec/`).
  app_updates' tools/gate.sh ran `exec python3 tools/check_roadmap.py --self-test` and then
  `exec python3 tools/check_roadmap.py` in one case arm. The second never ran, so the full gate
  never judged ROADMAP.md, only its self-test fixtures -- a gate that could not fail (fixed in
  app_updates 5a4b33c). shellcheck 0.11.0 exits 0 on that arm and on a top-level
  `exec echo a; echo b`. The class: a statement after a non-redirect `exec CMD` in the same
  block is unreachable. A block ends at `;;`/`;&`/`;;&`, `esac`, `fi`, `else`, `elif`, `done`,
  `}`, `)`, `then`/`do`, or EOF; blank lines and comments are not statements; a line
  continuation, a multi-line quoted argument, a `$( )`/`<( )`/backtick and a heredoc body are
  all part of the exec's own statement. Not findings: a redirect-only `exec` (`exec >log 2>&1`,
  `exec 3<&-`, `exec {fd}>f`); an `exec` joined by `||`/`&&`/`|` (also across a line break),
  backgrounded, or ended by `)` (`( exec x ); y`: only the subshell is replaced); and ONE bare
  `exit`/`exit N` after it -- the house parse-guard's `exec ... "$@"` / `exit` / `} # parse-guard`.
  Anything after that `exit` is still dead, and so is the rest of a subshell after its own
  `exec`. Scope is every shell source, the set early-exit-pipe reads; "what is a shell source"
  moved to `crates/goh/src/shellsrc.rs` so the two scanners share one definition, and the lexer
  gained `mask_top` so a newline inside a quote ends no statement. A HARD structural step in
  every repo at both scopes, not a ratchet: the sweep over all 30 `.gatesrc` repos (547 shell
  sources, 190 `exec CMD` lines) found nothing to migrate. `GOH_EXCLUDE` applies. RED FIRST: the
  table in `deadexec/tests.rs` ran red against a stub. The first mutation round left 6 of 17
  green: four were rules no row discriminated (rows added: `exec -a NAME` alone, a multi-line
  subshell after an exec, a `||`/`&&`/`|` at a line end before the exec, a `$( )` in the exec's
  own words) and two were DEAD logic, deleted -- a `case`/`(` context stack (the judgment reads
  only what ended a statement, and a pattern's `)` and a subshell's `)` both end the block) and a
  non-top token branch in the splitter. Final round: 18 mutations, every one red. The incident
  replayed: app_updates' pre-5a4b33c `tools/gate.sh` is red at `:54`, its fixed blob green; the
  table's first row is that arm. Pinned by `deadexec/tests.rs` and `tests/test_check_dead_after_exec.py`; the 23rd
  INVENTORY step.
* **`GOH_EXCLUDE` no longer reaches the secrets scan, and cannot.** docs/config.md said the key
  exempted the emoji scan, the length cap and unreaped spawns; the code applied it to ten checks,
  the credential scan among them. `app_updates` excluded a vendored crate from house style
  (`^vendor/camoufox-rs/`) and 57 of its 63 staged files went unscanned for secrets, with no
  output (2026-10-08; ztools had grown its own vendor secrets step over the same hole). The
  seam is removal, not a new key: `goh secrets` and its scanner take no exclusion at all, so no
  setting -- this one, a future one, or a script passing `--exclude` (now a usage error, exit 2,
  rather than an ignored flag) -- can drop it. A second key meaning "vendored" was the other
  option and was rejected: `GOH_EXCLUDE` would still have been able to drop the scan, so the
  defect would have stayed representable. A real vector in vendored code is fixed or carries
  `secret-ok: <reason>`, as first-party code does. `GOH_EXCLUDE` keeps every other exemption and
  the doc row now names them all; `tests/test_exclude_scope_doc.py` holds the row equal to the
  files that read the key, so the two lists cannot drift again. Pinned tests that encoded the
  exemption (`test_exclude_agrees`, `test_exclude_skips_matching_paths`, two CLI tests) now pin
  its refusal.
* **A direct `goh <check>` judges a repo as its own gate does.** Every subcommand with an
  `--exclude` ignored the repo's `.gatesrc` when called directly, so `goh.sh md-links` in
  app_updates reported a vendored crate's dead anchor that its structural gate exempts -- and
  every sweep that calls checks directly (the R3 estate sweep, gate calibration, the empty-scope
  sweep) judged that repo differently from the repo, refusing a gates_of_heck land on a green
  consumer. Unset `--exclude` now reads the repo's declared `GOH_EXCLUDE`
  (`gatesrc::declared_exclude`; the length cap's union with `GOH_LINE_EXCLUDE`), `--exclude ''`
  opts out, and the ambient environment still changes nothing. Red-first:
  `a_direct_check_judges_a_repo_as_its_own_gate_does`, and
  `test_goh_sh_reads_the_repos_own_exclusion` through `gates/goh.sh` (red on main's binary).
* **The R3 estate sweep judges a consumer's corpus with that consumer's `GOH_EXCLUDE`.** It ran
  each house check bare, so app_updates' vendored crate -- exempt under app_updates' own gate --
  carried a dead anchor that made the md-links corpus "NOT clean", and the sweep (and this repo's
  push gate, through it and through the empty-scope sweep) went red on a consumer that was green.
  The owner's key is read from its `.gatesrc` as data, never sourced, and passed to every check
  whose own `--help` takes `--exclude`, so no list here can drift from the binary; a plant never
  lands in an exempt file. The corpus helpers moved to `checks/_estate_corpus_io.py` at the
  500-line cap. Red-first: `test_a_path_the_consumer_exempts_is_not_judged_for_it`.
* **A commit whose only staged Rust is excluded is nothing to judge, not a blind scanner.** The
  commit-time "rust source policies (staged)" step decided it had work BEFORE applying
  `GOH_EXCLUDE` and ran the empty-scope check AFTER it, so `app_updates`' commit vendoring a
  crate was refused with "matched NO compiled source". The step now decides after the
  exclusion, and the refusal (`noallow::has_own_rust`, shared by `no-allow` and `empty-assert`,
  which had the same shape at `--staged`) fires only at full scope and only on a `Cargo.toml`
  the exclusion leaves in scope -- so a repo whose only Rust is vendored passes at push too,
  while a scanner blind to the repo's own crate is still red (`crates/goh/tests/exclusion_scope.rs`,
  red-proven both ways).
* **`goh commit-class --clusters --range REV...`: the fixes in a range, grouped.** Two fixes are
  one when their classes are (the commit gate's measure) or when they touched three of the same
  files; each connected group is a hardening candidate. A file in more than a quarter of the
  range (a ROADMAP) and a commit touching more than 20 files (a sweep) link nothing; in a range
  under 20 commits no file is common. On ZoneWM's labelled clusters, words alone keep 1 whole and
  files add the pictures and the teardown pair; the rest is paraphrase in different files, which
  no measure of words and paths sees. Its range is the fixture (`zonewm_range.tsv`).
* **A self-proof must leave the tree it ran over as it found it.** `check_probes_pass.py` runs
  every probe in parallel over the live tree; in koffee_big one probe unlinked a real source and
  wrote it back while another copied the tree, and the copy failed on the missing file -- a push
  refused on a clean tree, and, killed mid-plant, the owner's source deleted. The bytes came back,
  so git status was clean. Each file's inode, mtime and size are now stamped before and after;
  a change fails the gate, names the files, and a serial re-run names the probe. (From the
  koffee_big session, 2026-10-08.)
* **A commit hook's repository variables no longer reach what the gate spawns** (contract #12;
  media_server, 2026-10-08). From a linked worktree git hands pre-commit an absolute `GIT_DIR` and
  `GIT_INDEX_FILE`, and the stock hook passed both to `tools/gate.sh --staged` and its children: a
  consumer test that built a scratch repo wrote `core.bare=true` into the shared config, committed
  its scratch tree onto the worktree's branch and pushed three stashes. Every worktree commit also
  printed "the gates ... are NOT committed" (`git status` in the HEAD export answered with the
  consumer's index), and a consumer with a root `Cargo.toml` was refused outright: the version
  check read ITS `HEAD:Cargo.toml` and called the binary behind. Now `gates/_git_env.sh` is the one
  shell helper -- `goh_unbind_git` replaces seven inline `unset $(git rev-parse --local-env-vars)`
  sites (`scripts/build-goh.sh` keeps its own: it runs from a tree with no `gates/`) -- and both hooks drop the variables at entry (`goh_hook_unbind`: `hooks/pre-commit`,
  `gates/push_gate.sh`). The index `commit -a` / `commit <paths>` commits is not the repository's
  own, so it is carried as `GOH_HOOK_INDEX_FILE` and bound again, only in its own repository, by
  the staged readers (`structural.sh --staged`, `goh.sh`, `py_staged.sh`, `goh_index_view`, the
  proven key). A repository reachable only through `GIT_DIR` is refused by name. The gates' own git
  on their checkout drops the variables too, so the false warning and refusal are gone under the
  OLD hook as well; the scratch-repo corruption needs the new hook (`install.sh`, which the
  structural gate already asks an older stock hook for). Pinned by
  `tests/test_commit_hook_git_env.py`, each test red-proven.
* **`goh commit-class`: a repo's domain vocabulary is not a class.** A word in more than an eighth
  of the repo's classes (over its whole history; none below 30 classes) no longer counts toward
  "same class". ZoneWM's replay keeps its 3 right refusals and loses the wrong one, `078f137f`,
  which shared only `read` and `window`; the labelled pairs that matched still match. Its 75
  classes are the fixture (`tests/fixtures/commit_class/zonewm_classes.tsv`).
* **A git that refuses is an error, never an empty list.** `goh commit-class --range A..B
  --report` handed `--report` to git as a revision and printed "0 commit(s), 0 the rule
  refuses"; `goh shell-lint --staged` over a corrupt index printed "no shell files in scope". Both
  now exit non-zero with git's message.
* **`tools/land.sh`: a branch reaches main only through a green gate.** By hand it was `gate; merge`,
  and a `;` where `&&` belonged moved main onto a red gate run (2026-10-08). Now one command gates
  the tip and fast-forwards main to the gated SHA; a red gate, a tip that moved under it, or a main
  that moved on lands nothing. Pinned by `tests/test_land.py`, each refusal red-proven.

* **`goh requires-call`** (ZoneWM's proposal, opt-in by `GOH_REQUIRES_CALL`): "a Python file that
  CALLS X must also CALL Y", configured as rows in a TOML file, read from the AST so a docstring
  naming Y never satisfies it. A dotted name resolves through the file's own imports (another
  module's `verify` is not this one's); an exemption that outlived its reason fails; a rule that
  finds nothing to judge is exit 2. The parser is `ruff_python_parser` (Astral's own crate): over
  ZoneWM's `tools/` and this repo's 457 Python files it found the same 29,973 call sites as
  Python's `ast`, in 0.10 s. It brings `syn` 2 beside the crate's `syn` 3, build-time only (its
  derive macros). ZoneWM's selftest is lifted as the test; every branch is red-proven by mutation.
  A trigger can name a call's string ARGUMENTS -- `cli("config-set")`, or `*("theme", "--id")`
  for any callee -- matched side by side among its positional arguments, an argv list spread in
  place, so ZoneWM's courtesy rule ("a tool that drives the desktop waits for rest") is a row too.
  Over ZoneWM's tools it binds 12 of the 17 files its regex did; the other 5 name a verb only as
  text (a docstring, mutation anchors, controls' data). An argv built at run time is not seen.

* **A quiet host on demand: `tools/quiet.sh -- CMD`** (`lib/bench_lock.sh`). Every wall-clock number
  in BACKLOG needs load < 4, and two days of them were taken at load 7-31 beside other sessions'
  gates. Every gate now registers with a host-wide lock using bash builtins only (no process on
  the commit path). `quiet.sh` is a QUEUE that takes its place first: it holds the lock --
  running gates finish, new ones wait -- and the desktop, then waits up to `--settle` (300 s) for
  the load under `--max-load`, and runs CMD. A box still busy with what goh cannot hold (an
  `xctest`, Spotlight) is let go for `--retry` (300 s) and claimed again; only after
  `--deadline` (4 h) does it refuse, naming the busiest processes. Its first form waited for the
  load holding NOTHING, and on a box a dozen sessions share it waited 2 h without one window:
  each new gate started ahead of it (BACKLOG 3.1). A hold is short and says how long: CMD
  holds for at most `--hold` (`GOH_BENCH_MAX_HOLD`, now 900 s, was 3600), a waiting gate prints
  the hold's label and latest end, a longer hold is refused up front -- split it -- and a run that
  outlives its hold fails, because the gates resumed under it (3.2). A gate
  nested in a registered gate or in the measurement, and a dead, PID-recycled or overdue holder,
  never deadlock it. Pinned by `tests/test_bench_lock.py`, each case red-proven by mutation.

* **`goh early-exit-pipe`: no early-exit consumer on a pipe under pipefail** (`crates/goh/src/earlypipe/`).
  `producer | grep -q` with pipefail on is a race: the consumer exits at its first match, the
  producer's next write dies of SIGPIPE (141), and pipefail makes that the status -- so a match
  reads as a miss, on a loaded host only. Measured 2026-10-08: media_server's deploy.sh read a
  RUNNING service as absent, a `git diff --cached | grep -qE` decided whether a gate layer ran,
  and `ps | head -6` exited 141 here. Consumers: `grep -q`/`--quiet`/`--silent`/`-l`, `grep -m`/
  `--max-count`, `head` (not `-n -N`), awk `exit` outside `END`, sed `q`/`Q`; in EVERY shell
  source -- `*.sh`, `*.sh.tmpl`, `*.bash`, `*.zsh`, shell shebangs -- whether or not the file
  says pipefail itself: media_server's scan read only `*.sh` files saying "pipefail", and let a
  release through on a sourced install-lib.sh and the install.sh.tmpl that regenerates
  install.sh. Only a status something READS is a finding: a
  substitution used as an argument and a pipeline ending `|| true` pass. A structural step in EVERY
  repo, ratcheted like `vendored.rs`: a NEW finding is refused at `--staged`, existing ones are named
  at full scope, and `GOH_NO_EARLY_EXIT_PIPE=1` fails them all (this repo sets it). The fourteen sites
  here are fixed with here-strings (doctor, rust_gate, structural, release kit x2, profiling x8,
  screen linkage). Pinned by `earlypipe/tests.rs` (every rule mutation-proven red) and
  `tests/test_check_no_early_exit_pipe.py`.

## v0.24.0 — what one session costs the next _(2026-10-08)_

### 1. Measured: cross-session serialization

* **`tools/session_bench.py`** runs one workload in N isolated clones at once (N = 1, 2, 4, 8) and
  reports makespan, speedup, the Universal Scalability Law fit (sigma: serialized fraction; kappa:
  interference), every lock wait by name, and the steps that inflated most. First finding:
  `structural --full` sigma 0.51 with NO lock waited on, while spawn- and CPU-bound controls scale
  6-7x -- one run is 4.3 s user + 9.0 s sys. Sys-heavy work (spawns, filesystem metadata, a `ps` of
  the machine) is what fails to scale; the cuts below are that class.

### 2. Fewer spawns, every gate

* **The step wrapper (`lib/bounded_run.py`) costs half** -- 59.6 -> 26.4 ms a step, every step of
  every gate: no `ps -Ao` of every process when the step's group is empty (`killpg(pgid, 0)`), no
  `dataclasses`/`argparse`/`threading` on the common path, run under `python3 -S`.
* **One fact, one git spawn.** `goh` asks for the top level once per process (a staged structural
  run: 16 -> 9 git calls, ratcheted); the credential-URL step reads both key sets in one `git
  config`; git's repository-binding variables are asked once per process tree
  (`GOH_GIT_LOCAL_VARS`, trusted only when it names `GIT_DIR`) -- ~1300 spawns per suite run.

### 3. Rust coverage: one instrumented run, exported once

* The rust mode exported one lcov part PER TEST TARGET. Calibrated on all 29 media_server crates,
  one run of exactly the same targets gives the IDENTICAL merged report (same percentage, same
  line counts, every crate) in 173 s against 226 s. Every guard holds over the one run: the .ok
  marker, a failed run naming every target it lost, an empty run refused.

### 4. The rust proven cache learns a build's reads

* A crate whose build read a file outside its static scope was never recorded, so it ran on every
  push (media_server's vpn-watchdog-rs reads `compose/`). The compiler's dep-info names those
  files: an in-repo one is now learned and keyed from the next run on (the learning run records
  nothing; a learned file's edit re-gates). A read outside the repository is never learned.

### 5. The skills corpus at the moment it is written (C2, writer half)

* `hooks/claude/skill_edit.sh`, a Claude Code PostToolUse hook wired in `~/.claude/settings.json`:
  an edit inside the corpus runs `goh skills` (183 ms) and a finding goes back to the writer.

### 6. This repo's suite

* `-n 12` (faster than 8 once the spawns were cut), the `repo` fixture copied from a per-worker
  template (25.7 -> 3.0 s), the display-seam probe without git under every case (44 -> 4.5 s), and
  a session guard that fails the run when any test moves the checkout (`tests/_tree_guard.py`;
  one did: the release-hardening test rewrote the real `release.sh`). 94.4 s (v0.20) -> 65.8 s
  under load.

### 7. Correctness: a gate that said more than it did

* **Rust coverage floors that cannot be applied are refused**, exit 2, naming the key: per-target
  floors the rust mode never applied, and an unreadable floors file (a warning and a pass before,
  with the per-file check silently dropped). An `exempt` key relative to the project now names the
  same file in a push export as in the checkout; an absolute one held at one path only.
* **The HEAD export cache is pruned by last use and bounded in count**; an export used within the
  hour is never removed (the old prune went by creation time, after a week, unbounded).
* **A stop can no longer orphan the step it was sent to sweep.** A TERM/INT landing while `Popen`
  was still returning unwound with nothing to sweep, the step ran on, and the wrapper printed
  "swept"; a second Ctrl-C abandoned a sweep halfway. `bounded_run.py` holds a stop through both
  windows; `goh step` registers its listener before it spawns.

### 8. The orchestration native, where shell was the wrong tool

* **`goh step`** -- the ceiling wrapper (own process group, TERM -> grace -> KILL, the leak sample,
  GOH_TIMINGS): 26-79 ms -> 4.3 ms a step. Its one `unsafe` (the inherited signal disposition,
  `sigaction`; `killpg`) lives in `crates/goh-sys`, the workspace's only allowlisted unsafe crate.
* **`goh canary`** runs `local_ci.sh`'s steps: one process instead of a bash and a Python wrapper
  per step, the canary's contract pinned against both implementations.
* **`goh proven key|lookup|record`** -- the proven cache in-process, byte-identical keys and the
  same record format, so either side finds the other's records. The record directory is read
  from git's layout, not a `rev-parse`: a lookup 19 -> 3 ms.
* `goh.sh` resolves the binary once per process tree (a child call 78 -> 34 ms); `rust_gate.sh`
  execs it directly for its four native steps; `rust-scope` lists every package's sources in one
  git call (mediaops-rs 16 -> 2).

### 9. Caches that skip work only when nothing moved

* **`check_estate_corpus` remembers a verified entry** (keyed on each entry's scope at HEAD and any
  uncommitted edit under it, the checker, the binary): a warm run 1.49 -> 0.17 s, sys 3.86 ->
  0.43 s. Eleven ways a hit could lie were tested before the cache existed. Cross-session sigma
  for `structural --full` 0.45 -> 0.01.
* **Rust coverage is incremental**: only the profiles are reset, never the build; 29 of 29
  media_server crates give reports identical to the clean build, 173 -> 76 s.

### 10. The suite cannot drift back

* `tests/_drift_guard.py`: a `cargo` shim refuses a goh build that the caller did not declare
  (`DRIFT_BUILD_OK=1`), and a test over 60 s fails. `tests/test_suite_drift.py` ratchets `git init`
  sites per test file and machine-sized pools, and proves both guards red in a child pytest.
  `--strict-markers`. Their first run found ~75 tests racing a release build under `GOH_LIVE`
  (now built once, before the workers start) and a shadowed `pytest_configure`.
* **A throwaway crate builds into the run's own dir.** The shim points every workspace outside this
  checkout at `<run dir>/{workspace-path-hash}`, removed at exit (a caller's own build dir is kept).
  Before, each temp fixture crate left a cold build in `~/.cargo/build`: **10,450 dirs, 250 GB**,
  which filled the disk on 2026-10-06. A full suite run now adds none.
* Fixture repos copy a template `.git` (`tests/_fast_git.py`) instead of spawning `git init`.
* **Longest first** (`tests/_schedule.py`): tests ordered by the last run's own durations, so
  a long test never starts last and runs alone. The suite: 94.4 s (v0.20) -> 53 s under load.

### 11. The C2 hook judges a skill edited through Bash

* The matcher is `Write|Edit|MultiEdit|Bash`; a builtin-only fast path exits before Python when the
  command does not name the corpus -- 7 ms on an unrelated Bash command.

### 12. "Fix the class", asked where it cannot be skipped (opt-in, `GOH_COMMIT_CLASS`)

* **`goh commit-class`**, ported from ZoneWM: a `fix:`/`perf:` commit carries `Class:` and
  `Siblings:` in its trailer block; a class sharing two content words with two earlier ones
  carries `Systemic:` or `Filed:` with substance. Run by a new stock `commit-msg` hook and again
  over the pushed range by `push_gate.sh`, so `--no-verify` does not survive the push.
* **Calibrated on ZoneWM's real history** (54 classes, the pairs its author labelled): the
  prototype's "half the smaller class's words" matched 1 of 13 same-class pairs and none of the
  three third instances, because each class is written afresh and `that`/`what` were most of what
  pairs shared. A function-word stoplist, light stemming and two shared content words match 6,
  with 2 false matches in 1,418. Replayed, ZoneWM's history goes from 0 refused to 5: 3 right,
  1 arguable, 1 wrong. Paraphrase (the other 7 pairs) is beyond any word measure.

### 14. ZoneWM's generic tooling, centralised

* **`gates/round.sh -m <message> <path>...`**: one round end to end -- only the named paths are
  committed (`git commit --only`), the repo's own hooks gate the commit and the push, the push is
  pinned and read back from the remote. A named path HEAD alone has -- a `git rm`, the old side
  of a `git mv` -- is a deletion it commits, not "neither on disk nor tracked".
  **`GOH_ROUND_PREFLIGHT`** runs the repo's warm verify before the commit, so a red test stops
  the round in seconds rather than after the push's cold gate.
* **`GOH_MIN_FREE_GIB`**: `push_gate.sh` and `round.sh` refuse a cold gate on a short disk AS a
  short disk (`lib/preflight_disk.py`), before an export is made.

### 13. Consumers, fixed the day they reported

* **install.sh never silently bypasses hooks another manager owns** (zinc): live hooks in
  `.git/hooks`, another `core.hooksPath` or a `.pre-commit-config.yaml` refuse the install before
  anything is written; `--replace-hooks` takes them over, naming what stops running. Every stock
  hook in `hooks/` is installed -- the directory is the list.
* **A consumer's own pytest gets the HEAD export** (ztools: 53 of 120 tests refused since
  v0.22.0): the "test without GOH_LIVE" refusal is scoped to this suite (`GATES_OF_HECK_SUITE`).
* **Every retired checker answers by path again** (ztools: `check_no_secrets.py` and
  `check_no_home_paths.py` were deleted with no forwarder, and a consumer's gate died on them): a
  forwarder for all 22, pinned by a class test. **`--exclude` takes Python's look-around** again
  (`^(?!vendor/)` was refused by the native port's regex dialect): every consumer pattern
  compiles through one `PathFilter` (`fancy-regex`).
* **The commit gate runs the push's cheap Rust policies over the staged `.rs` files** (no
  `#[allow]`/`#[expect]`, no emptiness asserts): a commit was let through on an assert its push
  refused minutes later. Untouched files never block a commit.
* **A gate leaves nothing in `$TMPDIR`** (ZoneWM counted ~7,000 of our entries): the EXIT trap's
  unquoted `for` split `goh-python (staged).XXXX` on its space and removed nothing; the rust gate's
  scope file was never removed; the estate probe's checker dir and the empty-scope sweep's
  toolchain droppings (SwiftPM `TemporaryDirectory.*`) stayed. One newline-safe cleanup list
  (`goh_cleanup_add`), the sweep's gates under a TMPDIR it owns, kept-on-failure logs bounded
  (`lib/prune_kept.py`), and a 12 h backstop for killed runs; pinned by
  `tests/test_gate_temp_leaks.py`, which runs `structural --full` and asserts an EMPTY TMPDIR.
* **Every temp we put in the shared dir is claimable, and the suite sweeps it too** (ZoneWM,
  2026-10-07: ~1,500 `goh-*` entries still stranded a day later). The 12 h backstop only claims
  `goh-*` names, and only in a push's TMPDIR: release.sh's bare `mktemp`s, a test stub's and every
  `TemporaryDirectory()` were nobody's; the suite's `goh-test-git.*` seeds were never removed; and a
  suite run never swept. Every site now carries the `goh-` prefix (a ratchet scans tracked shell and
  Python for one that does not), the seeds go at worker exit, a failed release removes its archive
  dir, and the suite's controller runs the same backstop (`prune_kept.py --backstop`) at session
  start. Pinned by `tests/test_temp_claimable.py`.
* **The C++ coverage merge is bounded and judges only this run** (ZoneWM D-0245): `ctest` wrote one
  profile per test process (`%p`) and the merge took them all as argv -- ZoneWM's SwiftPM twin died
  at ARG_MAX at 6,965 -- unquoted, so a path with a space split. Now `%8m` pools them and the merge
  reads `--input-files`. Found beside it: a previous run's profiles were merged into this run's
  number; they are removed before `ctest`. Pinned by `tests/test_coverage_gate_cpp_merge.py`.
* **`release.sh`'s buffered copy no longer prints two shell errors**: the copy in `$TMPDIR` re-ran
  the HEAD trampoline, resolving `../../gates` there, on every release since `4cc8976`.
* **A `GOH_LIVE` run names its own tree as `GOH_DIR`**: unset, the binary's delegated checkers came
  from `~/Projects/gates_of_heck`, so a live run from a worktree swept main's `checks/`.
* **`--floors-json` is made absolute before a mode `cd`s into the project**; an `exempt` key is
  project-relative, now documented.

## v0.23.0 — the Python checkers are retired; the binary is the only tier _(2026-10-06)_

### 1. One structural tier: `bin/goh` (Phase N)

* **Every structural checker is native.** The twelve the native tier still delegated were ported,
  each parity-pinned against the Python on the estate and red-proven both ways: unreaped-spawn
  (2.87 -> 0.13 s on media_server), version provenance, kill-by-name, claim derivation, md-links,
  lock-version, credential-urls, python-formatted (spawns ruff, bounded), shell-lint (with a
  per-blob verdict cache: media_server `--full` 1.9 -> 0.30 s warm), dependency currency,
  empty-assert and the push gate's tag check. `goh <check> --help` is each one's usage.
* **The Python tier is gone.** `structural.sh`'s Python branch is deleted (498 -> 181 lines); with
  no binary, `structural.sh` and `goh.sh` REFUSE and say how to build one, and a failed rebuild
  refuses -- nothing else runs in the binary's place. `GOH_NO_NATIVE` is retired: said, and
  ignored. **Every consumer now needs a Rust toolchain**; `cargo` is a declared structural
  requirement and `build-goh.sh` refuses up front without it.
* **16 ported checkers, 21 helpers and `check_exclusion_has_ceiling.py` are deleted.** Six entry
  points that consumers call BY PATH stay as forwarders to `goh.sh <check>` (`check_no_emoji`,
  `check_file_length`, `check_python_formatted`, `check_version_provenance`, `check_tag_version`,
  `check_no_allow`); imported, they raise. `goh.sh` refuses the retired `--probe` and
  `--fresh-derivations` by name; `python-formatted --selftest` is native.
* **The spec is frozen, not deleted.** The parity suites run the Python exported whole from commit
  `96018bd` (`tests/reference_kit.py`); unit tables moved beside the Rust. The structural suite pins
  FROZEN verdicts and a named step inventory instead of tier agreement -- which found its
  python-format "red" case green in both tiers: it never opted in.

### 2. New refusals

* **R5: a new vendored copy of a house checker is refused** by the commit that adds it, and an
  existing one is named at full scope (`no vendored copies of house checkers`, the pipeline's 20th
  step).
* **C2: an external skills corpus is judged at its last COMMIT**, never its working tree -- another
  session's half-done skill edit refused a release here. The writer-time hook is an owner decision.
* **Allowlist entries for a deleted file are stale** (kill-by-name, claim derivation), an
  empty-scope excuse naming no gate is a finding, a `docs/map.md` row for a deleted file fails, and
  the calibration registry resolves a key to its native check.

### 3. Correctness found on the way

* `.gatesrc` is the only source of pipeline config: the native tier read `GOH_STEP_TIMEOUT` from
  the ambient environment and never from the file (`gatesrc::adopt_into_env`).
* The push gate said "tag does not match" over a tag check that could not run; the two read
  differently now.
* Five reference bugs the ports reproduced were fixed in both tiers first (a `--ratchet` crash, a
  semver comparator reading a missing minor as 0, a claim lost after a multi-line string, a
  traceback on a bad version source, an inert `--staged`).

### 4. Coverage counts a binary's tests from outside `cargo test`

`coverage_gate.sh --lang rust --external CMD` (`GOH_COV_RUST_EXTERNAL`) builds the binaries
instrumented, runs CMD with `$GOH_COVERAGE_BIN_DIR` naming them, and counts the profiles as one more
part; a failing CMD is a missing part, never a smaller report, and `rust_gate.sh` then keys its
coverage group on the whole tree. This repo's native checks are tested by the pytest suite, which
the floor never saw: `crates/goh` read 62.5% against its 95% floor until the suite was counted
(the suite's `goh` fixture takes the instrumented binary from `GOH_TEST_BIN`).

CMD runs in the CALLER's environment plus the profile destination: the instrumented build's
(`RUSTC_WRAPPER`, `CARGO_LLVM_COV*`, its target dir) leaked into a suite that builds crates of its
own made four suites red. While measured, a test that passes its own `env=` keeps
`LLVM_PROFILE_FILE` (conftest), so its runs count instead of littering `default_*.profraw`. The
`--locked` check now reads every gate script, not three: it found the `GOH_LIVE` build of `goh`
(`_goh_bin.sh`) resolving without `--locked`.

### 5. This repo's suite

Nine tests still set the retired `GOH_NO_NATIVE` -- two ran the same native pipeline twice, three
compiled `bin/goh` in a scratch clone per run; they name the session binary now, and a meta-test
refuses the key. 1806 tests, ~90 s at `-n 8` under load (summed test time 693 -> ~620 s). The 60 s
target needs a quiet-box measurement (BACKLOG).

## v0.22.0 — the shared checkout stops being live, and the gates run side by side _(2026-10-05)_

### 1. Consumers run HEAD, never the shared working tree (C4)

Every entry point (`structural.sh`, `rust_gate.sh`, `push_gate.sh`, `swift_gate.sh`, `py_gate.sh`,
`py_staged.sh`, `coverage_gate.sh`, `local_ci.sh`, `doctor.sh`, `proven.sh`) and the two sourced libs
consumers load (`_common.sh`, `tui/lib.sh`) re-run themselves from an immutable export of the gates
checkout's HEAD (`~/.cache/goh/head/<sha>`, `GOH_HEAD_CACHE`), built once per commit and renamed into
place whole. An uncommitted edit in the shared checkout -- three incidents on 2026-10-05 alone -- now
judges no consumer. `PYTHONPATH` gains the export first; `bin/goh` is found in the live checkout,
where C3 keeps it HEAD's. **`GOH_LIVE=1` runs the working tree on purpose** (developing the gates;
this suite sets it, and a gate started under pytest without it refuses). Cost: +45 ms per gate run.
Residual, stated in BACKLOG: three consumer call sites run a Python checker directly, and
`tools/*.sh` are not redirected.

### 2. Speed: the step list and the crate fan-out run side by side (P2, P1f, P1g)

* **`GOH_CI_JOBS=N`** runs `local_ci.sh` steps concurrently. Reports come out in DECLARED order, the
  fail accumulator and exit code are unchanged, and `[tag,...] cmd` steps sharing a tag never
  overlap. Serial stays the default: only the repo knows which of its steps write one file.
  `--jobs N` and `--steps-only` are for a caller that schedules for the repo.
* **`rust_gate.sh --each-crate [repo]`** gates every tracked top-level crate, biggest first,
  `GOH_RUST_JOBS` (default 4) at a time, through that same scheduler, with the whole-repo scans run
  ONCE (`GOH_RUST_GROUPS`). media_server, warm, proven off: 79 s against its hand-rolled
  `xargs -P 4` fan-out's 91 s; the repo scans 1 run instead of 29.
* **`GOH_RUST_LINT_CARGO=cargo-zigbuild`** runs the `--target` lint configs (only those) through
  zigbuild, whose `zig cc` builds ring's C for musl -- so a cross clippy can live in the crate's gate
  instead of a serial tail (media_server: 46 s). `required_tools` layer `rust-cross`.
  `GOH_RUST_LINT_CONFIGS` had no test at all; it is now calibrated (Linux-only dead code: host green,
  the musl config red).

### 3. Correctness

* **A red step's failure block quotes the step that FAILED** (ZoneWM H2, `lib/fail_lines.py`): a
  nested failure quotes the innermost dump alone and counts the rest; an unframed `make` log says
  so and puts make's `*** [target] Error N` first. The inner header used to match itself, too.
* **A stopped gate stops its steps.** A step in its own session never heard Ctrl-C or a TERM to its
  wrapper, and ran on unowned. `bounded_run` now sweeps its group on TERM/INT/HUP (an ignore
  inherited at entry -- nohup -- stays ignored), and `local_ci` hands the stop to every running step.
* **A step that exits fast still shows its leak, and a timed-out shell's worker is killed.** On
  macOS `getpgid` of a zombie is ESRCH, so a step that exited before the wrapper asked for its group
  had none: `bounded_run` sampled nothing (a leaked `sleep 1200` read clean on a loaded push) and
  `killtree` skipped the group kill when the shell had exited first. The group of a session child is
  its pid by construction (`killtree.session_pgid`); nothing asks any more.
* **The environment is not configuration** for `local_ci`: an inherited `GOH_CI_JOBS` /
  `GOH_CI_STEPS` is dropped (an exported job count had made every nested run concurrent).
* **`GOH_MAX_LINES=off`** declares no cap in one info line instead of a warning on every commit.
* `orphan_canary`'s header promised to report repo-named processes not observed under the step;
  it never did, and must not under concurrency. The header now says what the code does.

## v0.21.0 — measured first: the time was walks, rebuilds and serial waits, and a version is not a source _(2026-10-05)_

Every change below began as a number from the new instrument (`GOH_TIMINGS`, `tools/gate_profile.sh`)
on a real consumer push, and was proven against a test that failed before it.

### 1. The instrument (P0)

`GOH_TIMINGS=<file>` makes every bounded step of both tiers, every `goh_step` and every `local_ci.sh`
step append one JSON line (label, ms, rc, tier, parent label, real cwd); proven-cache hits are marked.
`tools/gate_profile.sh <repo>` runs a repo's push gate on HEAD, nothing pushed, and prints the
slowest steps and each label SUMMED across every place it ran -- the view that found a 7 s step
repeated in 29 crates, invisible on any single line.

### 2. Speed

| what | before | after |
|---|---|---|
| `goh lints`, one media_server crate (native / Python) | 2.42 s / 1.4 s | 0.05 s / 0.08 s |
| `dependency currency`, a small crate | 7 s | 0.09 s |
| `dependency currency`, routines (14 deps), warm | 0.97 s | 0.12 s |
| coverage, media_server `mediaops-rs` | 60 s | 27 s |
| coverage, routines | 144 s | 73 s |
| `structural.sh --full`, media_server | 4.75 s | 2.4 s |
| media_server push, `time-mini-rs` changed (13 crates use it): house rust-gate work | 362 job-s | 285 job-s, plus ~30 more from the shared repo scans |

* **Walks read git's listing, never the build tree** (P1a). `rglob("Cargo.toml")` descended 50,950
  directories of `target/` per crate and threw them away. One helper per tier; a class test plants
  an ignored build tree and refuses new disk walks under `checks/` and `gates/`.
* **crates.io answers cached per crate name, asked concurrently** (P1b). Only the report-only arm
  reads the cache; the fatal arm is offline, and goes red with the network seam broken.
* **Coverage builds once** (P1d). Each per-target `cargo llvm-cov` recompiled the workspace; now one
  clean, `--no-clean` per target, `clean --profraw-only` between them (`--no-clean` alone leaked one
  target's profile into the next). Merged reports byte-identical on both repos measured.
* **The native tier starts its delegated checkers together** (P1e), reporting in order; a red step
  still joins every checker it started.
* **`rust_gate.sh` proves each group once on its own inputs** (P3). Crate group keyed on
  `goh rust-scope` (the workspace, its `path =` packages, the config they read); repo-wide scans on
  the whole tree; coverage separately. A reach outside the crate that cannot be named widens its
  key to the tree; a build whose dep-info read a file outside the scope is never recorded. A reach outside the crate is
  RESOLVED to the file it names (`include_str!("../../../VERSION")` keys on `VERSION`), so 28 of
  media_server's 29 crates key on their own files; the 29th really reads the repo root.
* **What did NOT move, said plainly:** media_server's push wall time (~170 s) is now its own
  `scripts/dev/check.sh` (154 s alone: `repo_tests.sh` 98 s, a serial cross-target clippy 46 s); the
  house crate fan-out finishes first. The cross-clippy needs a cargo-command seam here (BACKLOG
  P1g); the tests are media_server's (servers ROADMAP O41).

### 3. Correctness

* **The ruff format check runs at commit time** (C1), over the staged index blobs, both tiers.
* **`bin/goh` is built from HEAD, stamped with its source, rebuilt when stale** (C3). A same-version
  binary lacking a step passed the version check; now `build.rs` stamps the git trees of its inputs,
  `build-goh.sh` builds an export of HEAD (uncommitted edits cannot reach it), and the resolver
  rebuilds a stale binary under a lock before using it. `GOH_BUILD_DIRTY` is gone with its cause.
* **A push whose branch moved while it was gated is refused** (ZoneWM D-0211): over HTTPS git sends
  the ref as it is AFTER the hook returns.
* **A failing test under coverage is named as one** (P1c, gate side), so a consumer's plain
  `cargo test` step can become `cargo test --doc`.
* **`GOH_DEPS_OFFLINE` / `GOH_DEPS_RATCHET` never worked**: the flags were passed quoted, as one
  argument with a leading space. Found by the P3 tests.
* `cargo-llvm-cov` is required up front; `git rev-parse` echoing an unresolvable revision under
  `pipefail` no longer kills a gate silently.

### 4. `gates/required_tools.{tsv,py}`

One published list of the tools a gate refuses without, one row per refusal site, pinned to the code
in both directions. `--repo . --install` prints the install commands for the layers a repo declares;
`--names` feeds any package manager; ruff is its own opt-in layer. Asked for by antiknob's CI.

## v0.20.0 — the ceiling cost 200 ms a step, and the gates broke under their own edits _(2026-10-05)_

The 2026-10-05 resume point said the suite hung. It did not: at v0.19.0 it finished in 109.2 s at
`-n 8`, 1453 passed. Every finding below came from measuring something end to end -- a gate run on
a one-file fixture, then three consumers' real pre-push -- rather than from reading code.

### 1. Speed: the costs were per STEP and per SPAWN, not per test

On this box sys time equals user time throughout, so process spawns are the cost metric.

| what | before | after |
|---|---|---|
| full suite, `-n 8 --dist loadgroup` | 109.2 s | 94.4 s (and 39 more tests) |
| `structural.sh`, Python tier, one-file fixture | 3.28 s wall for 1.5 s CPU | 1.60 s |
| `check_display_seam.py --probe` (the suite runs it 24 times) | 4.2 s | 1.4 s |
| `check_probes_pass.py` on this repo, probes only | 9.6 s | 3.5 s |
| R3 estate sweep | 9.4 s | 5.2 s |

* **`lib/bounded_run.py` polled every 0.2 s**, so every step of every gate in every consumer was
  reported up to 200 ms after it finished. It waits now, and reads the process table once at exit.
* **The display-seam probe made 6 git spawns per fixture** for a probe that never commits and drives
  full mode (which lists `--others`). One `git init`, copied. No case dropped; the 23-case rule-drop
  calibration still goes red on every rule.
* **Self-proofs and the estate sweep ran serially.** Both run on a thread pool now, reported in
  order; the sweep's output is byte-identical to the serial run (`checks/_ordered_pool.py`). Both
  proven concurrent by rendezvous, not by timing.

### 2. A running gate is pinned to the bytes it started with

routines' pre-push died with `coverage_gate.sh: line 485: syntax error near unexpected token ';;'`.
bash reads a script lazily by byte offset; this checkout was edited while a consumer ran it. Every
EXECUTED script (20) now wraps its body in a parse-guard brace group (`docs/contracts.md` 19).

### 3. A missing tool and a moving lockfile are failures

* `cargo-machete`, `swiftlint` and `shellcheck` absent each printed a warning and exited 0. They
  fail up front now (`goh_require`). The shell-lint test that asserted the degrade pinned the defect.
* `rust_gate.sh` had no `--locked` anywhere, so a stale `Cargo.lock` was silently rewritten and the
  gate went green (antiknob carried `tools/lock_guard.sh` against it). It now refuses a missing lock,
  runs `cargo metadata --locked` first, passes `--locked` to every resolving call, and checks the
  lock is byte-identical at the end (`docs/contracts.md` 20).

### 4. The push gate leaked, and built cold every time

In `~/.cache/goh/push/`: a three-day-old export worktree and 230 `*.out` files; in
`~/.cargo/build/`: **24 orphaned build-dirs, 30 GB**, because cargo keys the build-dir by
`{workspace-path-hash}` and the export path was random -- every push built every dependency cold.
A run now owns `<root>/<repo>-<hash>/<repo>` with an owner stamp (pid + start time): stable per
repo, so dependencies stay warm; a concurrent push takes a private path with its build-dir inside
it; cleanup NAMES what the gate wrote outside its tree (Finance's tests write `"$HERE.out"`); and
each run reaps runs whose owner is dead, which `git worktree prune` never did (`docs/contracts.md`
21). The litter was removed.

Consumer pre-push end to end: media_server 201 s before; 242 s on the first claim of its stable path
(cold), **197 s warm**, green. routines 303 s, green (its first claim). ztools stays red on its own
clippy 1.99 findings. The shared structural layer costs ~3 s in each.

### 5. Scope

* **Build scripts are under the line cap**, both tiers: `Makefile`, `GNUmakefile`, `makefile`,
  `CMakeLists.txt`, `[Jj]ustfile`, `*.mk`, `*.cmake` (ZoneWM's 698-line Makefile passed). The two
  copies of the list are compared whole by a test. Newly over: CadGoose/CMakeLists.txt (702),
  games/CadGoose2/CMakeLists.txt (1142), games/necrohand/Makefile (511).
* **`check_probes_pass.py` names the self-proofs it does not run** (non-`check_*` files with a
  probe flag; 23 in ZoneWM). Discovery stays by name: ZoneWM's `input_lock.py --probe` locks the
  owner's real input.
* **`credential.helper` and `http.*.extraheader`** are judged with `check_no_secrets.py`'s
  patterns plus a helper's literal `password=` and the credential after an auth scheme
  (`checks/_credential_config.py`). The probe is 32 shapes.
* **Stale hooks say so.** Hooks are copied, not delegated: 14 repos still ran the pre-`0ac0f70`
  pre-push, which gates the working tree. `structural.sh` names an older stock hook with the
  `install.sh` command; `retired_hooks.sha256` gained the two shipped versions it was missing, and
  a test derives every shipped version from history. ztools' two hook fixes are upstreamed.

### 6. One session's uncommitted edit no longer stops the estate

The binary-currency check read the shared checkout's WORKING-TREE `Cargo.toml`. Bumping it ahead of
the release commit refused every consumer's commit (ZoneWM reported it within minutes). It reads
HEAD's version now; uncommitted gate source keeps its own by-path warning
(`tests/test_binary_currency.py`, which no test had pinned before).

### 7. A gate finds its own runtime, and a crash is not a refusal

`~/.zshrc` exported `PYTHONPATH=$GOH_DIR`, and non-interactive shells never read it (ZoneWM had to
export it in its own Makefile). `gates/_common.sh` now exports it for everything a gate runs. Under
an empty PYTHONPATH the empty-tree sweep's copied checkers all died on `ModuleNotFoundError` and the
sweep scored every crash as a healthy refusal: it now names a Traceback as "crashed", and supplies
the copied checkers' runtime (`tui/`, `lib/`) itself. That honesty surfaced three checkers that
demand an argument by design (excused, with reasons) and one real finding, fixed:
`check_no_credential_urls.py` printed `OK` in a repo with no remote because it had judged the
machine's GLOBAL helpers. Those are still judged; the repo is "not applicable" unless its own config
holds something. A checker that dies on the empty tree ITSELF (app_updates' `check_literal_list.py`
reads its subject unconditionally) still counts as refusing; one that imports its repo's own package,
which a skeleton cannot hold, is named "not measurable on an empty tree" rather than failing.

### Decided

* `GOH_EXCLUDE` keeps `re.search` (substring) semantics: every consumer's patterns are written for
  it. ztools anchors its own pattern (`'^vendor/'`).

### Dependencies

`cargo update`: nothing newer. numpy 2.4.3 -> 2.5.3; the golden tier stays bit-identical.

## v0.19.0 — the 5x was not the table, and a credential in a remote URL was invisible _(2026-10-05)_

Two findings, one measured and one proven, and they point the same way: **every question this estate
asks about a repo, it asks about the wrong tree.** v0.18.0 grew `check_no_unreaped_spawn.py`'s
measured table from 36 shapes to 55 and the pytest suite went 226 s to 1190 s. The obvious reading is
that the table is the cost. It is not.

### 1. The suite was slow because the R3 estate sweep ran against throwaway fixtures

Measured, one contributor at a time:

| what | cost |
|---|---|
| `check_no_unreaped_spawn.py --probe` — **55 shapes** | **0.06 s** |
| `check_estate_corpus.py` — 8 real consumer corpora | 73.4 s |
| `check_probes_pass.py --root <this repo>` | 116.8 s |

The probe is in-process analysis over source strings; 55 shapes cost a sixteenth of a second. **The
table is the point of the gate and it was not shrunk** — it is 55 measured shapes and every row that
grew it was a shape the checker had been calling clean.

The minutes were `check_probes_pass.py` running the R3 corpus sweep on *every* invocation, whatever
`--root` named. A run against a tmp estate holding **one trivial gate** cost **53.7 s**, of which
53.66 s was that sweep; the same command with `--no-corpus` takes **0.03 s**. The sweep's subject is
the checkers in *this* `checks/` directory — `--root` names a tree whose self-proofs are being swept,
which is a different subject. So:

* `check_probes_pass.py --probe` cost **54 s**, because the probe calls `main()` three times on a
  fixture and each of those swept eight real consumer repos;
* five tests asserting that a **fixture's** registry is sound were paying for those eight repos, and
  would have gone red because a `media_server` commit landed.

Scoped now to the repo that owns the checkers, with `--estate` to force it, and the skip is **stated
rather than silent** — `docs/SUPERSOTA.md` **R4**'s rule is that a step which does not run must be
visible. Nothing is cached: a foreign root re-derives every time, and the sweep's content is still
proved entry by entry by `tests/test_estate_corpus.py`.

### 2. A quadratic in the spawn checker, 23.2 s → 2.1 s over the same corpus

`rust_findings` re-derived `guard_types(crate)`, `drop_types(crate)` and `_known_types(crate)`
**once per file** over a crate context that is the same string for the whole scan. Measured over
`media_server`'s `crates/`: a per-file call cost **134x** one without the context, and one pass over
that 626-file tree took **23.2 s** where it now takes **2.1 s**. The arithmetic is deliberately
unchanged — same sets, same subtractions, same unions — because a cost fix that moved a rule would
be two changes in one commit.

The memo is held to the same bar as the gate it speeds up. Breaking it to ignore its key turns three
tests red including `--probe`, and the failure reads:

    ✓ [no_unreaped_spawn] OK — 3 test file(s), every spawned child reaped on a panic
    assert 0 == 1

A gate reporting success over a shadowed empty `Drop` it cannot see — the cached-clean-over-nothing
failure `check_empty_scope.py` exists to catch, reproduced inside the memo. Verified by **differential**
as well as by the table: the pre-fix checker tree and this one, over all eight real corpora with the
plant in each, produce identical finding lines and identical exit codes.

| suite, 8 workers, `--durations=0` | before | after |
|---|---|---|
| total test time | 1379.8 s | **735.6 s** |
| wall clock | 233.6 s | **105.7 s** |
| `test_gate_calibration.py` | 387.0 s | 33.3 s |
| a pass over media_server's 626-file `crates/` | 23.2 s | 2.1 s |

Not all of that was the regression, and the remainder is named rather than implied:
`test_goh_structural_parity.py` (121.9 s) and `test_display_seam.py` (108.8 s) are now the largest
contributors and this release does not touch them.

### 3. `check_no_credential_urls.py`: no gate could see a credential in `.git/config`

`check_no_secrets.py` reads git-**tracked files**. `.git/config` is untracked by definition, so a live
credential in a remote URL survived every sweep the estate ran — **proven in a scratch repo**: a
`gho_` token in `remote.origin.url`, and `check_no_secrets.py` reported `✓ OK`. A test now asserts
that sibling verdict, so this gate's reason to exist cannot quietly become false.

**26 measured shapes**, in the table, in the docstring and in `--probe`, every one read back through
git's own config reader and CPython's `urlsplit`. Three of them are measurements of things the
obvious implementation gets wrong:

* **`remote\..*\.url` does NOT match `remote.<n>.pushurl`** — the obvious regex returns nothing for a
  push URL, so the *write* side of every remote is invisible.
* **git LOWERCASES the variable name of a subsection key.** `insteadOf` is stored and read back as
  `insteadof`, so a case-sensitive match finds nothing — and that is the one shape a remote must look
  clean to carry a credential in, because the credential sits in the **key**.
* **The fingerprint is over the whole userinfo.** `url.split("@")` splits on the first `@` and reads
  `p@ss` as the host; the report then names a host that does not exist while still exiting 1. Pinned
  to the digest rather than to an equality between two runs, because a comparison cannot see a split
  that is wrong in both of them.

**The credential is never printed** — a `sha256` fingerprint of the userinfo plus file, remote, host
and reason. The estate learned this with `gho_` and `ghp_`.

**A rule was removed, and that is the finding worth recording.** The first version had a scheme
allow-list (`http`/`https` only). `ssh://git@github.com` is userinfo and must stay silent — handled
by the shape test instead. But the allow-list would also have called `ssh://gho_<36>@github.com`
clean, and measured against a real `ls-remote`, git reads an `ssh://` userinfo as the SSH **username**
and hands the rest to `ssh`, which ignores a password: **not a live credential on the wire, and still
a plaintext secret in `.git/config`**. A test pins the refusal so the weaker rule cannot come back.

Silent on `ssh://git@host`, `git@host:path`, `file://`, a local path, `git://`, an IPv6 literal and
`https://someuser@github.com/...`. Red on a password, on a bare credential-shaped userinfo, and on a
token in an `ssh://` remote.

**Deliberately not scanned**, each with its reason in the docstring and in `docs/BACKLOG.md`:
`.netrc`, CI configs (a token there *is* committed, so `check_no_secrets.py` already has it), shell
history, `credential.helper` and `http.*.extraheader`. The last two are measured to carry tokens and
are the real next step — they want `check_no_secrets.py`'s existing credential-named-key rule applied
to `.git/config`, not a third variant of it.

Wired into `gates/structural.sh` (layer 1) and the native tier, and registered in
`checks/gate_calibration.json` per **R7**. That entry also corrects the `no_unreaped_spawn` claim,
which still said 37 shapes after the table grew to 55. The native step is **delegated, not ported**:
every other native step ports a Python checker over files the binary has already read, this one reads
`git config`, and duplicating a 26-row credential table in Rust would be the second copy of a rule
about credentials.

### Also

* `checks/_estate_sweep.py` — the R3 corpus scoping, split out of `check_probes_pass.py` at the
  500-line cap and cut at a real boundary: R3 corpus is not probe discovery.
* `cargo update` — nine crates moved to latest (`clap` 4.6.6 → 4.6.7, `libc` 0.2.189 → 0.2.190,
  `rustix` 1.1.4 → 1.1.5, `syn` 3.0.5 → 3.0.6, and five more).

## v0.18.0 — the unit of protection is the BINDING, and a masker that lost the file _(2026-10-05)_

Three confirmed blind spots in `checks/check_no_unreaped_spawn.py`, all found by **consumers with
real fixtures** rather than by this repo. They are one class, and it is the class `docs/SUPERSOTA.md`
**R3** exists to name: *a gate that passes on a shape it cannot actually see.* v0.17.0 shipped this
checker calibrated against its own table, reporting `36 shapes green` across 32 wired repos, while
missing leaks that two independent repos found in the same week. A silently blind gate is worse than
no gate, because it converts an unknown into a false pass.

**1. A guard on one variable laundered a second, unguarded one.** Protection was a property of the
enclosing FUNCTION: if the function mentioned a guard type anywhere, every spawn in it was guarded.
`routines`' `tests/control_lifecycle.rs:227` and `tests/control_takeover.rs:190` each spawned a **raw,
unguarded** `/bin/sleep 300` owner beside a `Reap`-wrapped server and reaped it below
`wait_for_socket`'s `assert!`. **The gate reported both clean.** Fixed by attaching protection to the
**binding** the spawn's result was assigned to — and because a wrapper's ownership is often a
`Self(...)` tuple field, `Self` is resolved through the enclosing `impl` and *that* type's `Drop` is
read. The same attribution now applies to every other protection: an `EXTERNAL_REAPERS` call or a
deadline watchdog has to name that binding, or a pid derived from it. Both were laundering a second
child exactly as the guard did.

**2. The Rust masker desynchronised on a raw string and blanked the rest of the file.** The closing
delimiter was matched against the whole OPENING token (`r#"`, `r##"##`, `br#"#`), so
`r#"{"op":"shutdown"}"#` never closed and every remaining line was blanked before the spawn scan saw
it. **Nine spawn sites in `routines`' tests were invisible to this gate**, which is why both files
above read clean twice over. State is now `(close, raw)`. The `raw` half is a genuine Rust/Python
difference and was **measured against both toolchains rather than reasoned about**: `r"a\"` is valid
Rust, `r"a"b"` is not, and `r"a""` is ONE string in CPython. The first attempt branched on `r` in
the Python masker and blanked four real literals; the interpreter refuted it and the branch is gone,
pinned by a calibration row so it is not re-derived.

**3. A `Drop` that does nothing was accepted as a guard.** `Self(` was honoured whenever ANY
`impl Drop` existed in the file, so with **`monitor`'s guard `Drop` body emptied to nothing the gate
still reported clean** — precisely the failure `monitor`'s own `a_panic_after_the_spawn_leaves_no_child`
test was written to catch, and the gate could not catch it. `Self` now resolves to a type, and a
resolved type whose `Drop` neither kills nor waits is a finding.

**Sibling bugs, closed rather than left for the next reader.** A guard carrying a lifetime
(`impl Drop for X<'_>`) was invisible to the old regex; `cmd.spawn()` behind a `&mut Command`
parameter — how a guard's own constructor spawns — was not recognised as a process spawn *at all*;
`_fn_bounds` picked the nearest preceding `fn`, which for a guard declared inside a test is `fn drop`
twelve lines above the spawn; the guard-construction window was 8 lines and the incident's own
repaired file has a nine-line comment between the spawn and `ReapOnDrop(child)`. A cross-file
`tests/common/` guard was invisible per-file, which reported `routines`' **fixed** code as broken, so
guard types are now gathered at **crate scope** — while a LOCAL `impl Drop` still overrides it, since
the compiler uses the local one.

**A shape a text pass cannot judge is now reported, not passed.** `unjudgeable` is counted and named
and never failed on: a wrapper whose `Drop` is in another crate is a dependency's contract. Passing
it silently is the blind spot this release exists to remove; failing it would teach everyone to ignore
the gate — the same reasoning as `is_locked`'s "an unanswerable question must not read as free".

**Measured, then fixed, then measured again.** The estate was swept before and after, all **32 wired
repos**, honouring each repo's own `GOH_EXCLUDE`. **Sites policed rose 27 → 30** (+11%): `monitor`'s
guard constructor and `routines`' two raw-string-hidden sites, all three now judged correctly. Exactly
**one new finding**, `games/ZeroThunder`'s `tests/e2e/garden_drag_flicker.py:73` — a **real** dropped
handle on the LIVE tier that the estate has been carrying unfixed, now visible because Python's panic
scan had never counted a bare `return` as an exit that skips a reap, the way Rust's always did. Three
shapes the same widening got wrong were caught and corrected against the real files: `return proc` is
a handoff, a `return` under `poll()` is a child already gone, and a `try:` ABOVE the spawn reaps.

The measured table (SUPERSOTA **R2**) is now **55 shapes**, and **18 of them are rows that were
measured CLEAN before this fix and FINDING after it** — run as two isolated processes, because a
comparison harness that shares modules with what it compares cannot see the difference it exists to
measure. Every new Rust row is verified to **compile** under rustc 1.99.0 and every Python row to
**parse** under CPython; `rustc` is what disproved the fifth "desync" case, which turned out to be the
checker's own bug wearing a fixture's clothes.

Files: `checks/check_no_unreaped_spawn.py`, `_spawn_mask.py`, `_spawn_lex.py`, `_spawn_rust.py`,
`_spawn_guard_attr.py`, `_spawn_reap.py`, `_spawn_py_sh.py` (replacing `_spawn_shapes.py`),
`_unreaped_spawn_probe.py`, `_unreaped_spawn_table_regressions.py`, `tests/test_no_unreaped_spawn_estate.py`.

## v0.17.0 — an unreaped child can never again be invisible, and every wait is bounded _(2026-10-04)_

`media_server`'s `archive_torznab` test spawned the real binary with `--bind
127.0.0.1:0` — a server that loops forever by design — and reaped it with an
explicit `child.kill(); child.wait();` **sitting below four lines that can
panic**. **Nine live processes** accumulated, each holding the cargo build lock,
so every later `cargo test` blocked producing **no output at all** for 30
minutes on a suite that finishes in **0.22 s**. The hang was the leak's
*symptom*; the leak was invisible because the run that would have reported it was
the run that had been killed. It cost a day.

Four things, because the class has four faces.

**1. `checks/check_no_unreaped_spawn.py`** (+ `_spawn_mask`, `_spawn_shapes`,
`_unreaped_spawn_probe`, `_unreaped_spawn_table*`) — a test that spawns a child
nothing reaps **on the panic path**. Rust, Python, shell. The ordering rule is
the point: the reap *existed* in that file, so "is there a kill in the function"
answers yes and the file is clean. A guard is the only shape that survives a
panic — a `Drop` impl that kills or waits, `with subprocess.Popen(...)`, a
`try`/`finally` that reaps, a shell `trap` — and a guard is judged by **what it
does**, never by its name, because the second defect in that same file had
already been "fixed" by a rename.

The measured table (SUPERSOTA **R2**) is **36 shapes run as `--probe`**, every
disposition measured against rustc 1.99.0 by a PID-liveness probe. Two rows are
measured *decisions*: `.output()`/`.status()` reap by **blocking** and are not
findings, because flagging them flags the fix — and the unbounded wait they do
instead is item 3 below, not this gate's business.

The instrument was wrong twice before the table was right, and both corrections
are the point: a name-matching probe reported `survivors=0` for every arm because
a copied `/bin/sleep` is killed by the kernel at exec (`rc=137`), so it was
measuring a process that never existed; and a first fixture for the incident
dropped the `.expect()` calls the gate exists to find, so the checker reported the
incident **clean**.

Calibrated against all 32 wired repos before landing: **44 raw hits → 5 real
leaks in 3 repos**, after seven checker defects the sweep itself exposed — each
fixed at the rule, none exempted. File-granular instead of region-granular
`#[cfg(test)]` scope; `try`/`finally` not modelled as Python's `Drop`; a `Popen`
stored or returned not modelled as a handoff; a `Drop` that only *waits* not
recognised as a guard; a `Child` handed to `wait_timeout()` not recognised as
reaped; a deadline watchdog not counted; and `Self { child }` not recognised as a
guard construction. The leaks: **3** in `routines/tests/follow_lifecycle.rs` (a
`sleep 300` with asserts between the spawn and the kill — the incident's shape,
in the wild), **1** in `monitor/crates/multitop/src/ssh/ssh_tests.rs`, **1** in
`games/ZeroThunder/tests/e2e/live_probe_lib.py`, and **1 in this repo's own
`tests/test_release_hardening.py`**, whose `finally` restored a script and left
the process running. Re-measured 2026-10-04: ZeroThunder is fixed (`a6f6962`),
so **2 leaks in 2 repos remain**, and their gates refuse their own commits. See
`docs/BACKLOG.md`.

The checker is **wired**, not filed: `gates/structural.sh:460` (layer 1, every
repo) and the native tier's `steps_delegated::step_unreaped_spawn`. It carries a
`--probe` that goes red when the ordering rule is switched off at its own named
seam — measured: *"2 of 36 measured shapes disagree with the table"* — and a
`check_estate_corpus.py` entry that plants the incident's ORDER inside 621 real
files of `media_server`'s `crates/` and requires the gate to go red and name the
panicking line and the reap below it.

**2. The drop guard is named.** `ReapOnDrop`, its contract spelled out in the
checker's docstring, and its absence a finding wherever a raw `Child` is spawned
in a test.

**3. Every wait is bounded.** `lib/bounded_run.py` runs a command in its **own
process group** and sweeps the **whole subtree** on expiry — TERM, grace, KILL —
then prints `TIMED OUT after Ns` with the survivors named and returns **124**.
Measured against `bash -c 'sleep 400 & wait'`: `local_ci.sh`'s old `pkill -P
"$pid"` sweep left **2 grandchildren ALIVE**, i.e. the mechanism meant to unstick
a hung step left running exactly the orphans that make the *next* step hang.

Ceilings are now **ON**: `GOH_STEP_TIMEOUT` **1800** (`goh_step` had **no**
timeout while `local_ci.sh` had one — one hang, two answers) and `GOH_LCI_TIMEOUT`
**900** (unset in every repo in the estate, which meant *no limit*). Both are
printed on every step line; `0` opts out and says `UNBOUNDED` rather than looking
identical to a bounded step; a non-numeric value is exit 2, because a typo'd
ceiling must not silently leave the step unbounded. The **native** tier had no
bound either and now routes every delegated step through the same
`lib/bounded_run.py`, so all three paths share one implementation of the ceiling
instead of three that could disagree — and `test_goh_structural_parity.py`
compares the tiers' step inventories **as sets**, which is how the gap was found.

**4. `lib/orphan_canary.py`** brackets every step, because the ceiling cannot see
this case: the step exits 0 and the server outlives it. A leak now exits **125**,
distinct from 124 and from the step's own code — "the suite passed and left a
server running" and "a test failed" have opposite fixes, and one exit status for
both sends someone to the wrong one.

**It attributes by PROCESS GROUP, because everything cheaper was a rumour.** The
first version diffed the whole process table either side of a step and called "a
new process whose ppid is 1" an orphan. True alone, machine-global in practice:
under this repo's own parallel suite every run reported the *other* workers'
deliberately-leaked processes, and three end-to-end tests went red for a reason
that had nothing to do with what they measured. The replacement — a descendant
walk at step exit — was **blind for exactly the case the tool exists for**: a
leaked child is reparented the instant its parent exits, so `ps` shows `ppid 1`
and the walk finds nothing. Measured: the canary reported a clean run while
`sleep 422` ran on. The process *group* survives reparenting (`64886 1 64885
/bin/sleep 422` — ppid gone, pgid intact), so ownership became a fact rather than
a guess, and there is no second "probably ours" category: a verdict built on two
kinds of evidence is a verdict whose wrong answers nobody can tell apart. The
limit is named rather than papered over — a child that calls `setsid()` leaves
the group. Consequently **one** invocation per step (`take`/`since` are gone),
and everything not attributable is counted in one line and never failed on,
because a canary that cries wolf is one nobody leaves switched on.

**Four more defects the wiring found, each of which would have shipped:**
`if ! cmd; then rc=$?; fi` in `goh_step` read the status of the `!`, so a step
that died came back **successful** and the gate ran on; `output=None` had been
collapsed into "discard", so `goh_step` — whose contract is capturing a failing
step's output — captured **nothing** and reported "failed" against an empty log;
a `case` arm read bare `$GOH_LCI_TIMEOUT` while its subject read
`${GOH_LCI_TIMEOUT:-900}`, so the new default was an unbound-variable crash under
`set -u` in every repo that never set it; and the canary's own `int`/`str` pid
mismatch made its self-exclusion subtract nothing, so every green step failed,
attributed to the canary watching it.

### The kill-by-name gate was blind to `/usr/bin/pkill`, and four of them were ours

`check_no_kill_by_name.py`'s word boundary was `(?<![\w./-])`. The `/` in that
class meant **a path-qualified command is not the command**: `/usr/bin/pkill`,
`bin/pkill`, `./pkill` all went unreported — in shell, in a Python argv list and
in a Swift `executableURL`. The gate printed *"OK — 306 tracked code files, no
kill by name"* over a tree holding four of them, **in its own tests**.

Measured on both boundaries over 11 estate repos: here the old boundary finds 0
of the 4 and the new one finds all 4; across the estate the delta is exactly one
further hit — ZoneWM `Sources/zt-agent/DesktopShortcutMonitor.swift:121`,
`process.executableURL = URL(fileURLWithPath: "/usr/bin/killall")` with
`arguments = ["Dock"]`. A true positive, and one this gate was built to catch.

The self-proof is why it survived: `test_every_claimed_shape_is_red_and_named`
listed ten spellings and every one was the bare word. Five path-qualified shapes
are now claimed, and the gate goes red without the fix — measured **10 hits where
it must be 15**. Grepped the siblings for the same blindness: `_spawn_shapes.py`
and `check_no_allow.py` need their `.` to avoid `foo.Popen`, and
`check_no_home_paths.py` matches the path itself. Only this one carried a `/`.

Those four cleanups were killing by name, which is what made a second suite on
this box go red: `pkill -f "sleep 1200"` matches a full command line, and the
canary's own argv spells the command it wraps, so the cleanup SIGTERMed the
canary that had just reported the leak. Two concurrent suites at HEAD gave 8 and
5 failures in two different sets, three at `returncode -15`. Each test now reaps
the pids the canary published through `--snapshot`, or walks down from a pid it
owns. **The reap is load-bearing:** with it stubbed to a no-op the suite still
passes green and leaves 3 orphans behind — the process table, not the test, is
what shows the cleanup happened. Two concurrent suites, twice, on this tree:
**1316 passed, 0 failed, four runs out of four**; the same experiment before:
8 failed, 5 failed, 3 failed, 0 failed. *(Those four runs are the measurement
`56ee59f` recorded on 2026-10-03; this release did not re-run the concurrency
experiment, and the release gate ran the suite once, not twice at once.)*

`docs/config.md` already stated the contract this code violated ("no `pkill` or
`killall` by name … in tracked code and scripts"), with no carve-out for a path.
The doc was right, so no doc changed.

### Two corrections to the measured table, because a spec table that disagrees with its own probe is worse than no table

A row said kill-without-wait was a finding; the measurement says a bare `kill()`
leaves a **ZOMBIE**, and a zombie cannot hold a build lock, cannot outlive the
run and cannot make a suite hang. The row now says clean, and says why in the
place a reader arguing with it will actually look. And the table was **37 rows
and 36 shapes** — two byte-identical `kill() with no wait()` cases left over from
an earlier edit, so the probe printed 37, asserted 37, and measured 36 distinct
shapes. A duplicated case is not harmless in a table whose whole job is to be the
specification: it inflates the count a reader trusts, and it is the one kind of
duplication that would make a narrowed rule look better covered than it is.

### Docs

`docs/BACKLOG.md`'s estate index is **re-measured rather than remembered** (every
row by running the house checker in that repo, read-only), and two of its rows
were stale: ZeroThunder's leak is fixed at `a6f6962` and its "19 files dirty"
warning is withdrawn; `monitor`'s path is the full
`crates/multitop/src/ssh/ssh_tests.rs:88`. The `ztools` exemption is **measured,
not believed** — planted in a throwaway copy of ztools' tracked tree, never in
ztools itself: the real tree is green *and still examines 106 of ztools' own test
files*, and a violation planted in one of them is still **red** — while the same
violation at a path that merely *contains* `vendor/` is **silently exempt**,
because `GOH_EXCLUDE` is `re.search`, a substring test. Harmless today; the
anchoring is now written down in `docs/config.md`. `docs/SUPERSOTA.md` §3 is
re-dated and records that **no ranked item moved** in this release.

## v0.16.0 — a repo's own config stops deciding its gate's verdict _(2026-10-03)_

SUPERSOTA **R3** — house checkers measured against real corpora, and R3's own
first failure recorded (the gate caught itself).
SUPERSOTA **R5/R6** — the display-seam gate, and ZeroThunder's two vendored
checkers retired after a capability diff.
SUPERSOTA **R7** — the calibration registry is read here, and every claim in it
is checked.
SUPERSOTA **R4** — a step that does not run is now a failure, and the two tiers'
step inventories are compared as sets.
SUPERSOTA **R8** — the failing tier names the checker and routes to its rule.
The push gate stops handing the export gate this repo's `.gatesrc`.
Uncommitted gate source: publish refuses, certify names itself.


SUPERSOTA **R3**: "a gate that cannot fail in the shape it was written for is
not proven." The mechanism for the estate as a whole lives in
`games/game_asset_factory/tools/check_gate_calibration.py`. What lands here is
the part that can be checked from this side.

`checks/check_estate_corpus.py` takes a **real subtree copied out of a real
consumer repo**, plants one violation inside a real file of the estate's own
language, and requires the checker to go red AND name the planted file. Six
entries, each recording which corpus and why. Copies, never in place — these are
other people's working trees.

**It found its own first version proving nothing**, which is the part worth
reading. The gate reported 6/6 green while `check_no_empty_assert.py` was
replayed with the 2026-10-02 receiver bug — the class that omitted `"` — because
(a) the plant was the wrong shape (`assert!(v.is_empty(), "msg")` is the shape
*clippy* is silent on, not the receiver bug), and (b) the corpus carried a
finding of its own, so the output named the file whether or not the checker had
seen the plant. A red that cannot be attributed to what caused it is not
evidence.

So the gate carries two rules it did not have:

* **the corpus must be CLEAN under the checker before the plant lands.** Not
  clean → refuse the entry, because a plant's verdict there is unattributable;
* **a corpus below a file floor, and a plant that would land in a stub, are
  refusals.** A "real corpus" that quietly degraded to one file is the failure
  this gate exists to catch, reproduced inside the gate.

And the `check_no_empty_assert` entry's plant is now the shape it actually
failed in — a string literal *inside* the receiver — with the replay as the
demonstration:

    # _RECEIVER = r"[A-Za-z0-9_.:()\s-]+"   (the 2026-10-02 bug, replayed)
    x check_no_empty_assert.py: a violation planted in 9 real file(s) from
      ~/Projects/servers/storage-server was NOT caught (exit 0)
    x 1 checker(s) did not refuse a violation planted in the real estate.   rc=1
    # restored
    v 6/6 checker(s) went red on a plant inside real estate corpora          rc=0

Three corpora were rejected while choosing the table, each for a reason worth
keeping: ztools is red under `check_no_allow` (a vendored tree carries ten), eight
candidate repos are red under `check_no_home_paths` before any plant lands, and
scoping the `check_md_links` entry to `docs/` reported two "findings" that were
artefacts of the subtree copy breaking every link that escapes it.

`--probe` proves the two refusals and that a checker which always exits 0 is
caught. It runs from `check_probes_pass.py`, so it runs on every gate run in
every repo — and pays for a corpus copy only on a machine that has the estate.

**What this does NOT do, stated rather than implied:** it cannot judge its own
plant. The plant is authored beside the checker, which is precisely how the first
version proved nothing; a plant derived mechanically from a real finding needs a
language-aware mutator per checker, and that is not built. And it measures HOUSE
checkers — the consumer class in R3's table (`gluetun_socks5`, read the last
`image:` in a 40-service compose file) is still unmeasured here. **R3 is narrowed,
not closed**, and `docs/SUPERSOTA.md` R3 plus `docs/BACKLOG.md` say so in those
words.

### The display-seam gate, and ZeroThunder's two vendored checkers retired

SUPERSOTA **R5/R6**. `games/ZeroThunder` carried repo-local copies of two house
checkers, both sha-diverged. The rule is that a checker one repo needs is added
here and wired into that repo; the caution is that a local copy may be doing
something the house one does not, so diff **capabilities**, not bytes.

**`check_no_conflict_markers.py`** needed nothing. The house copy is a strict
superset of the local one's detection (`\s` or end-of-line after the marker, not
just a space) and additionally reads THE INDEX in `--staged` where the local copy
read the worktree. Deleted; ZeroThunder now runs the house one, which
`gates/structural.sh` was already running for every repo anyway — the local copy
had been invisible precisely because it duplicated a gate that already ran.

**`check_no_screen_presentation.py`** was not a copy of the house checker of that
name. Different rule, different scope: the house gate refuses screen APIs in TEST
TARGETS; this one refused them in APP SOURCE unless they routed through a seam,
and exempted the seam's own file. Widening the house gate would have deleted a
capability, so the capability moved here as its own gate.

`checks/check_display_seam.py` — on-screen presentation and real-input use route
through a seam the consumer names; a live-tier harness must CALL the helper and
carry a substantive, non-placeholder reason; every tracked `*.sh` is policed
whatever the sweep roots say. The consumer's sweep roots, seam, exemption list
and live-tier vocabulary are policy: `tools/display_seam_policy.json`, committed
in the consumer and read by the house gate. `docs/new-checker.md` §3 forbids
per-repo policy inside shared code; a policy file is the other half of that.

Ported across, none of which the house checker had: the input tier
(`NSEvent.mouseLocation`, event taps, `pressedMouseButtons`, cursor warp/hide,
`.post(tap:)`), `NSWorkspace.shared.open` of a system pane, the `input-ok:`
marker, **justified** markers (a bare `// screen-ok:` silences nothing — that
rule moved to `checks/_marker_reason.py`, and ZeroThunder's `tools/marker_reason.py`,
imported by eight of its gates, is now a re-export of it rather than a fourth
copy), a live declaration that must be a call rather than a mention, the executor
allowed on an **earlier line** than the command (the shape `ruff format` emits,
and which the house checker is blind to), the global shell pass, and an inline
self-proof.

Measured and deliberately NOT ported: the local `APP_LAUNCH` regex also matched
`pkill -f .*ZeroThunder`. `pkill` belongs to `check_no_kill_by_name.py`, which
already owns it; a kill pattern inside a policy file that gate does not read
would be the bypass, not the port. No finding is lost — the house gate was run
over the whole tree before and after.

**`--probe` is 58 cases, and every rule is calibrated.** One case per pattern in
the Swift and live-command vocabularies (a pattern nobody drives is a pattern
nobody knows works), plus the config refusals. `tests/test_display_seam.py`
re-runs the probe against a copy of the real module with each of its 23 rules
neutered, so dropping one turns its own row red and nothing else:

    swift-input-family    -> Swift: reading the real pointer is refused
    swift-tap-family      -> Swift: CGEvent.tapCreate is refused
    masking               -> Swift: a string naming a shape is prose, not code
    justified-marker      -> a BARE screen-ok marker silences nothing
    lookback              -> a MULTI-LINE subprocess call is caught
    helper-must-be-called -> merely DOCUMENTING the helper is not a declaration
    live-reason           -> a PLACEHOLDER live reason is a violation
    shell-pass-global     -> an unguarded .sh is a violation, wherever it lives
    offscreen-declared    -> a SECOND offscreen mode inherits no recognition
    unknown-key-refused   -> an unknown policy key is refused, not ignored
    zero-scope-refused    -> a policy matching no source is REFUSED, not passed

ZeroThunder's coverage afterwards, planted and caught, not assumed: a planted
`makeKeyAndOrderFront` in `UI/` (red, and red again through `--staged`); an
undeclared `screencapture` harness in `tests/`; an unguarded `osascript` at the
repo root; a **bare** `// screen-ok:` — the hole the original gate was measured
against; and a planted conflict marker caught by the house marker checker.

### The calibration registry is read here, and every claim in it is checked

SUPERSOTA **R7**. `checks/gate_calibration.json` had held 23 entries claiming
named gates had proven they can fail, and nothing in this repo read it:
`check_probes_pass.py` sweeps what a gate *declares*, and the registry was not
part of what a gate declares. The only reader lived in another estate
(`games/game_asset_factory/tools/check_gate_calibration.py`, which is also the
cross-repo survey and remains so).

The defect is sharper than "a file nobody reads". The sweep runs what
`discover()` finds, so a checker that stopped dispatching on `--probe` simply
stops being run — silently, with no output, while the registry goes on calling
it proven. Measured on this tree: stripping the `--probe` dispatch from
`check_md_links.py` took the sweep from 11 self-proofs to 10 and left the gate
**green**.

`checks/_calibration.py` reads the registry and holds every entry to the estate:

* the key must name a gate that exists (estate-independent — it holds on a
  machine that has never heard of the other estates);
* a cited prover must resolve, and be **committed at HEAD**: a file on disk is
  not a claim, it is a rumour its author alone can check;
* a prover inside this repo must have had the claimed self-proof run green by
  the same sweep;
* a prover outside this repo must list the key among what it proves
  (`--proves`), and its estate is named in the output rather than assumed.

It runs from `check_probes_pass.py`, so it is read on every gate run in every
repo; a consumer repo with no registry is reported, not failed. It is not a
second copy of the external survey — two numbers that can disagree are worse
than one.

It also found a live inconsistency on its first run: `known_unproven` spelled
its key `check_swift_coverage` where `proven_by` spells the same gate
`swift_coverage`. Nothing had read the file.

Self-proof: `checks/_calibration_probe.py`, split out so neither file crowds
the 500-line cap, driven by `check_probes_pass.py --probe`. Fourteen cases —
every way the registry can lie asserted red, the sound registry asserted clean —
and `tests/test_gate_calibration.py` re-runs the probe with each rule
neutered, so the reader cannot lose a check quietly.

## v0.15.1 — two version layouts a release could not be checked against _(2026-10-02)_

`v0.15.0` shipped with the emptiness-assert gate and the declared Python rule
set. These two are the same class of defect found afterwards, and both of them
arrived from an agent working nearby rather than being commissioned — read
before keeping, and named here so the record is honest about it.

**`plist`** — an Apple bundle declares its version as a `<string>` under a
`<key>` in `Info.plist`, which was none of the layouts this gate shipped with.
Measured on ZeroThunder, whose `v2.10.0` tag no strategy could check:
`file:` reads the XML **declaration** as the version
(`('(file)', '<?xml version="1.0"…?>')`) because it takes the first line it does
not read as a `#` comment, compares the tag against "1.0", and reports a correct
release as a mismatch; `swift:` and `xcconfig:` correctly find nothing, because
there is no `let` and no `SETTING =`. So a repo that declares its version the way
Apple ships it was UNVERIFIABLE, while this gate's own rule is that an absent
version source is a finding rather than a pass.

**`pyproject`** — the `[project] version` a Python distribution declares. Same
shape of blindness: without a strategy for it, a packaging-only repo has no
readable source at all and `file:` reads whatever the first meaningful line
happens to be.

One entry in `STRATEGIES` each, no new code path. What I added to both:
`docs/config.md` enumerates the kinds, and a kind nothing documents is
half-wired.

## v0.15.0 — the emptiness assert clippy cannot see, and a declared rule set for Python _(2026-10-02)_

`check_no_empty_assert.py`, wired into every Rust repo's gate ahead of clippy.

The class it closes was not hypothetical and not one repository. Four repos in
two days: media_server 77 files, routines 50 assertions, app_updates 17,
gates_of_heck 24 — and every single one of them was a release blocker rather
than a broken build. media_server's `bump.sh` refused to cut for exactly this
reason, so a stylistic lint held a release.

What makes this a gate and not another sweep is that **clippy is blind to half
the shape**. Measured against clippy 1.99.0, one shape per line:

    assert!(v.is_empty());                        assert_is_empty
    assert!(!v.is_empty());                       assert_is_empty
    assert!(v.len() == 0);   /  assert!(0 == v.len());   len_zero
    assert!(v.len() > 0);                         len_zero
    debug_assert!(v.is_empty());                  assert_is_empty
    assert!(v.is_empty(), "with a message");      NOTHING
    assert!(!v.is_empty(), "msg");                NOTHING
    assert_eq!(v.len(), 0);                       nothing (clippy's own suggestion)

The two silent rows are why the class kept recurring: an assert carrying a
message is the form people write on purpose, and it passes. That is not a
theory here — clippy was green on this repository while **5 instances sat in the
tree**, two commits after a sweep for the very same lint. Those five are fixed in
this change, which is the first evidence the gap was real and the second that
the sweep was incomplete.

The rows that stay clean matter as much: `assert_eq!(x.len(), 0)` is not a
violation, it is what clippy asks you to write, and a checker that flagged it
would be flagging the fix. Both directions are cases in `--probe`, which is
registered in `gate_calibration.json`, so a future narrowing of the match list
turns a gate green again by going blind — and the calibration test breaks the
pattern on purpose to prove the probe notices.

Also in this change, both from the same session's findings rather than from
preference:

* `checks/_rust_text.py` — the comment-stripping depth counter, the string-literal
  regex, the compiled-source scope rule and the `@generated` marker, shared by
  `check_no_allow.py` and the new checker. Two checkers needing the same three
  things is how a third copy appears; the second one to need it did not write one.
* `qbittorrent.rs` and `screen.rs` move their tests to submodules. Both sat at
  the 500-line cap with tests inline, and twice in one session a test edit pushed
  a file over it. The cost of that class is paid by the next change, at commit
  time, in the gate.


## v0.14.0 — four gate gaps an independent audit found, closed structurally _(2026-10-01)_

This stanza did not exist when the tag was cut. The release bumped the workspace
to 0.14.0 and shipped without one, so the changelog's newest entry was v0.13.5 —
a released version with no record of what it contained. Written from the tagged
commit rather than from memory, and deliberately short: the four fixes are
described where they live.

* A Python tree with no formatter has its shape decided by whichever hand last
  edited it. `check_python_formatted.py` now runs ruff over a tree that declares
  no configuration, and says which rule set it applied instead of letting the
  default decide silently.
* The lockfile said 1.35.0 while the manifest said 1.36.0. `check_lock_version.py`
  reads both and fails on the disagreement, because that is the shape of a
  release cut from a stale lock.
* `check_tag_version.py` refuses to push a `v<semver>` tag whose target commit
  declares a different version — the defect that made a published tag able to
  name a commit whose own build reported itself as another version.
* A gate that scanned nothing read exactly like a gate that found nothing.
  `check_probes_pass.py` runs every gate's own self-proof, after 24 of them were
  found to be green by luck rather than by working.

## v0.13.5 — a tag is a claim, and it is checked against its own commit _(2026-10-01)_

`check_tag_version.py` refuses to push any `refs/tags/v<semver>` whose target
commit declares a different version. It exists because of media_server, today:
a version was bumped, `git commit --amend --no-edit` was REJECTED by pre-commit
twice, and `2>/dev/null` swallowed both refusals so the amend looked like it
had worked. `git tag -f v1.79.3` then named commit `a266067`, whose VERSION file
and all 31 crate manifests still said 1.79.1, and `git push --follow-tags`
published it. Anyone checking out `v1.79.3` got a build that reported itself as
1.79.1 — a name that cannot tell two different binaries apart, except the name
outlives the mistake. The repo's own `test_native_version_flag` DID catch the
mismatch; it ran in the pre-push gate, but nothing tied it to the TAG.

Three properties, each a place the obvious implementation is wrong:

- **The refs being PUSHED**, from the hook's stdin, not every tag in the repo.
  Scanning all tags lets one stale tag veto every unrelated push until somebody
  deletes it — a gate that cries wolf gets `--no-verify`'d.
- **The version at the COMMIT** (`git show <commit>:<path>`, `^{commit}` to peel
  an annotated tag), never the working tree. The tree is not the thing being
  published; in the incident it held the fix the amend was about to lose.
- **An absent version source is a FINDING.** `v2.0.0` on a tree that declares no
  version is unverifiable, and unverifiable read as fine is how the next one
  ships.

Version sources are a table, not a repo. Both live layouts ship by default — a
`VERSION` file (media_server) and `[workspace.package] version` (app_updates) —
and `GOH_TAG_VERSION_SOURCES` in `.gatesrc` points it anywhere else, with
opt-in globs expanded against the tree at the commit. Globbing is scoped on
purpose: a bare `**/Cargo.toml` sweeps `vendor/`, and app_updates' camoufox-rs
declares 0.1.0, which is not that repo's release number. A glob matching nothing
is reported, so a typo cannot retire a strategy in silence.

It runs in `gates/push_gate.sh` FIRST, over the refs as given, because both
skips below it are about CODE: a commit the remote already holds, or one already
gated in this push, would otherwise let a lying tag through unexamined — and
re-tagging an old commit to a new version is exactly how a lie gets published.
`push_gate.sh` also now captures stdin to a file first, since a stream read
twice is a stream read once.

Calibrated red on the real input (`refs/tags/v1.79.3` → `a266067`), green on a
matching pair, red on a dirty working tree that claims the right version, and
red on a tag with no version source. The probe is discovered by
`check_probes_pass.py` like the others, and the case is pinned in
`tests/test_check_tag_version.py`.

It also surfaced a class defect on the way. Every gate that runs a Python
checker out of this checkout writes `__pycache__/` into it, and the proven-step
cache keys on that checkout's untracked contents — so a checkout WITHOUT this
repo's `.gitignore` (an installed copy, a CI export) changed identity part-way
through a push, and every step the pre-commit hook had proved re-ran in the
pre-push export. `_proven.sh`'s gates identity now excludes `__pycache__/` by
path segment (a substring filter would silently drop real work, and a file
genuinely named `__pycache__.py` is not a cache). Bytecode is not work in
progress on the gates; a real edit to a checker still invalidates the cache,
and both directions are pinned in `tests/test_proven.py`. Found by adding one
call to `push_gate.sh` and having an unrelated test go red.

## v0.13.4 — two new shared gates, both canaried rather than exempted _(2026-09-30)_

`check_version_provenance.py` asserts that a version string references a git
hash and a build date, and that something sets them. It is STATIC on purpose:
gating on `binary --version` actually printing a hash cannot run on every
repository — wrong architecture, missing toolchain, a build that costs minutes,
a crate with no binary at all — and a check that gets skipped everywhere is
worse than no check. So it asserts the DECLARATION, which is cheap, portable,
and still fails for the reason anyone wanted it: a version written without
provenance. Exit 2 means "cannot tell", which is not the same as pass.

`check_swift_warnings.py` judges one `swift build --build-tests` whose whole
output is read once with every ANSI colour and OSC hyperlink stripped first.
Two earlier gates split the job and neither could do it: the concurrency grep
matched diagnostic group names that `swift build` wraps in terminal hyperlinks,
so the name never appeared whole; and check-deprecated ran its own second build,
which on the cold pre-push tree had nothing left to compile and printed no
warnings at all. "Every warning fatal" was therefore never true at push — found
in ZoneWM 2026-09-26, where a `self` captured in a `@Sendable` closure in a
test, and an unneeded `nonisolated(unsafe)`, both passed it. A build that fails
fails the gate, because files it never compiled printed no warnings.

Both landed UNPROVEN and the calibration ceiling was already spent, which turned
every consumer's push red rather than one. Both are static, so both are
fixtureable, so both are now canaried — red on a violation, green on a clean
tree — which puts the ceiling back where it was instead of raising it to two and
teaching every repo that the number means nothing. `--staged` was added to the
provenance checker because the hook already passed it.

## v0.13.3 — a hook's GIT_DIR never reaches git run on another repository _(2026-09-27)_

Git hands a hook `GIT_DIR` (pre-commit also `GIT_INDEX_FILE`). Under a
LINKED worktree `GIT_DIR` is absolute, so a checker that ran `git init` on a
scratch repo inherited it and re-initialised THE REAL REPOSITORY instead —
and with `extensions.worktreeConfig` on, wrote `core.bare = true` into the
shared config. Every checkout of that repo then died with `fatal: this
operation must be run in a work tree` (zinc, 2026-09-27). These gates run
inside every consumer's hooks, so one miss flips any repo, in every worktree at
once, from a command that looked like it was building a fixture.

Git on the repo BEING gated keeps the variables — `GIT_INDEX_FILE` names the
index being committed, which is exactly the tree to police. Git on any OTHER
repo, and every process run inside one, now drops git's own list from `git
rev-parse --local-env-vars`, never a hand-kept copy (the one in `_proven.sh`
had drifted to 7 of git's 15): `checks/_gitutil.py`'s `foreign_repo_env()`,
used by `check_empty_scope` for the skeleton's init/config/add/commit AND for
the sweep whose gates otherwise measured the real tree, and by
`check_probes_pass` for every `--probe`; `gates/push_gate.sh`, whose export ran
with the pushing worktree's `GIT_DIR`; `gates/_proven.sh`; `goh-testkit`'s
`git_command()`/`git_in()`, which replace four hand-rolled unscrubbed test
helpers; and `tests/conftest.py`, at import, before any fixture.

Pinned by `tests/test_hook_git_env.py` and
`crates/goh-testkit/tests/hook_git_env.rs`: a scratch main, a linked worktree
and worktreeConfig on, with `test_the_fixture_reproduces_the_class` asserting
the raw inheritance really does flip `core.bare` — so a passing test cannot be
the harness being vacuous. Each route was red-proven by removing its own scrub.
Two proofs were blind at first (`--show-toplevel` answers the cwd under a
leaked `GIT_DIR`; the sweep's 100-char echo cut both paths before they
differed), and a census test pins every `Command::new("git")` in `crates/` so a
new unscrubbed fixture helper fails. Contract #12 in `docs/contracts.md`.

**If a repo of yours ever printed `must be run in a work tree`, check it:**
`git config --file "$(git rev-parse --git-common-dir)/config" --get core.bare`
answers `true` for the residue, and the same command with `--unset core.bare`
repairs every linked worktree at once (reproduced and repaired on a scratch
repo for these notes; `git status` fails before, succeeds after).

This release also ships the four stanzas below, which were written but never
tagged: v0.12.6, v0.13.0 (the Rust port), v0.13.1 and v0.13.2.

## v0.13.2 — no process kill by name _(2026-09-27)_

`checks/check_no_kill_by_name.py`, opt-in per repo with
`GOH_NO_KILL_BY_NAME=1`, fails on a `pkill` or `killall` that matches by
name, and on a `pgrep` or `pidof` that feeds a `kill`. A name matches
processes the caller does not own. On 2026-09-23 zinc's T6 cleanup ran
`pkill -9 -f camoufox` and SIGKILLed another session's Gemini browser in the
middle of a reply, which stopped the necrohand campaign. Owner-scoped forms
pass: `pkill -P`, `-g` and `-s`, and `killpg`. Comments, docstrings and prose
are not kills.

Survivors go in `kill_by_name_allow.json`: one exact line in one file each,
with a reason and a status. `legitimate` is a decision. `unreviewed` is debt,
and every run counts it. A stale entry fails.

Seeded the same day in the seven repos without an active session: 52 entries
covering 58 lines, in this repo, CadGoose2, ZeroThunder, necrohand, divoom-control,
homebrew-tap and sys_updater. Surveyed but left to their owners: ZoneWM (8
hits) and koffee_big (4 hits, a tree dirty with someone's work).

The native pipeline delegates this step to the Python checker, as it does
shell lint. Parity is pinned both ways, and GAF's canary proves the check
bites.

## v0.13.1 — a gate over the working tree says when the tree moved under it _(2026-09-27)_

A gate that builds and tests the WORKING tree certified whatever bytes sat
there while it ran: an edit mid-run killed ZoneWM's `make verify` with
`error: fatalError` in a target nobody touched and no cause named, and a
green run over a moving tree certified bytes no commit holds.
`lib/tree_stamp.py` stamps (mtime, size) of every file git would show at the
start and compares at the end; `goh_tree_stamp` (swift, rust and python
gates) refuses a pass over a moved tree and names the move on a red one.
The staged gates do not stamp: they judge the index, so another session's
edit elsewhere is not theirs to refuse. Tool caches (`__pycache__`,
`.pytest_cache`, `.build`, `target`, lockfiles a build writes) are gate
output, never a move -- found by this repo's own py and rust gate tests.
Consumers with their own runner call the CLI (ZoneWM's `make verify`).

## v0.13.0 — the Rust port: native layer 1, bit-exact golden, one resolver _(2026-09-27)_

Every checker a shared gate runs is now native (`goh`) with the Python
checker as its parity-pinned fallback: home-paths, ceiling + ratchet,
no-allow, screen presentation, lints opt-in and the skills corpus joined
emoji / markers / length / secrets (1.6x-15.6x per step). A staged run reads
each index blob once and exports shell files once: 197 git spawns on a
50-file commit became 6 (~2.3 s -> 0.24 s). `goh golden` is bit-identical
to the numpy tier and decodes as Pillow does. `gates/goh.sh <check>` is the
one way a consumer runs a house checker; `gates/_goh_bin.sh` the one binary
resolution. `scripts/build-goh.sh` never publishes `bin/goh` from
uncommitted goh sources (`GOH_BUILD_DIRTY=1` to override), and no test run
can publish it. Plan retired: `git show 15004a5:docs/rust-port-plan.md`.

## v0.12.6 — a hook from an older stock is pristine, not "locally modified" _(2026-09-27)_

`install.sh` refused to update three repos whose hooks had never been
touched: they were on the previous stock hook, installed before the
install record existed, so the hook matched neither the current stock
nor a record. `retired_hooks.sha256` lists the digest of every stock
hook ever shipped (append-only); a hook matching one is overwritten
without `--force`. Found the day v0.12.3's pre-commit change (route
through `tools/gate.sh --staged`) needed to reach every repo -- until it
did, every staged language layer was decorative at commit time there.

## v0.12.5 — a failed coverage export shows its own output _(2026-09-21)_

`coverage_gate.sh --lang rust` sent every `cargo llvm-cov` export to
`/dev/null`. When one failed -- monitor's `local_agent_test`, once, at
push time, and green on every rerun -- the gate said "export failed" and
nothing else, so the defect reached the push with no evidence and had to
be reproduced by hand before it could be looked at. The export's output is
kept beside its part (`part-<pkg>-<kind>-<name>.info.log`) and its tail is
printed on failure. `test_failed_export_shows_its_own_output` pins it
(proven red on the old gate).

## v0.12.4 — the coverage build pins the BUILD dir, not only the target dir _(2026-09-21)_

`coverage_gate.sh --lang rust` isolated the instrumented build with
`CARGO_TARGET_DIR` alone. Since cargo's `build.build-dir` (the house layout
since 2026-09-20 puts every crate's intermediates under `~/.cargo/build/`),
the target dir holds only final artifacts; the test binaries cargo-llvm-cov
exports from live in the build dir, shared with every ordinary build of the
crate. The gate therefore merged the instrumented binaries of the PREVIOUS
source into the report - lines past the end of the current file, all
uncovered - and read a 97.5% tree as 93.5% (routines). The gate now exports
`CARGO_BUILD_BUILD_DIR` equal to its own target dir; the stub-cargo test
records the environment of every build-driving call and refuses any call
that sees a different build dir. Proven red on the old gate.

Also: **the Swift gate asks where the codecov payload is instead of
guessing.** Swift 6.3's build system writes it to
`.build/out/Products/<config>/codecov/`, not `.build/<triple>/debug/codecov/`,
so `swift_gate.sh` reported a green `test + coverage` step followed by "no
codecov payloads match" (antiknob, blocking its push). The gate now passes
`swift test --show-codecov-path`'s answer to `check_swift_coverage.py`
(`--spm-glob`, repeatable), and the checker's default tries both layouts.
Tests: `test_spm_finds_the_swift_6_3_products_layout`,
`test_spm_glob_names_the_exact_payload`.

Also: **`no_allow` sees a suppression wrapped in `cfg_attr`.**
`#![cfg_attr(target_os = "macos", expect(unsafe_code, ...))]` is the
attribute with a condition on it, and the literal grep for `#[expect(` never
saw one; monitor carried eleven that way. The wrapped form is matched by the
`allow(`/`expect(` token inside an open `cfg_attr(` attribute, across
rustfmt's multi-line layout, with string literals ignored. Tests:
`test_cfg_attr_wrapped_suppression_is_refused`,
`test_cfg_attr_without_a_suppression_passes`.

## v0.12.3 — the commit hook goes through `tools/gate.sh --staged` _(2026-09-21)_

`hooks/pre-commit` exec'd `structural.sh --staged` directly, so a staged
language layer wired into a repo's `tools/gate.sh` (which the starter invites:
"the cheap half of a language gate runs at COMMIT time") never ran on commit -
a commit gate weaker than the push gate, the hole rule 14 names. Found in
media_server when a Rust gate under `--staged` was added and a crate's red
gate still committed. The hook now execs `tools/gate.sh --staged` when the
file exists (structural alone otherwise), symmetric with pre-push. Test:
`test_pre_commit_runs_the_repo_gate_staged_layer` (a layer's marker must
appear on commit; a red layer must block it) - red before the fix. Existing
repos pick the new hook up on their next `install.sh` (stock hooks are
replaced; locally modified ones are refused as before).

Also: the repo's own `crates/goh/tests/common/mod.rs` carried six
`#[expect(clippy::expect_used)]` - exactly what v0.12.2 started refusing, and
the push gate said so. The fixtures are now `crates/goh-testkit`, a
dev-dependency crate whose manifest states the test policy `clippy.toml`
already declares for test code (`allow-*-in-tests`) but cannot reach helper
functions in an integration-test crate; one `must()` helper stops a test with
the reason. No suppression attribute anywhere in the tree.

## v0.12.2 — `#[expect]` is a suppression too _(2026-09-20)_

`checks/check_no_allow.py` now refuses `#[expect(...)]` and `#![expect(...)]`
beside `#[allow]`, and its scope covers `tests/` and `examples/` as well as
`src/`, `benches/` and `build.rs`. `#[expect]` cannot rot (it errors when the
 lint stops firing) but it still ships the finding - a cast that "fits today",
 a function that is "only 104 lines", a `#![allow(dead_code)]` on a shared
 test fixture that should be a dev-dependency crate. Policy from the servers
 Rust campaign (operator, 2026-09-20): fix the finding properly. Tests:
 `test_expect_is_refused_too`, `test_tests_dir_in_scope` (the two inverted
 cases from v0.12.1's suite).
- **MCP handshake LATEST is 2026-07-28 (SEP-2575).** `lib/mcp_scaffold.py`
  answers `initialize` with 2026-07-28 when asked, echoes every version in
  `HANDSHAKE_VERSIONS`, and falls back to LATEST on an unknown or missing
  version (three new cases in `tests/test_mcp_scaffold.py` pin each arm).
  Consumers inherit it at runtime via `GOH_DIR` with no reinstall:
  koffee_big, necrohand and ZeroThunder speak the new version on their next
  run. 2025-11-25 stays supported (still echoed), just no longer latest;
  nothing in docs/ or lib/ pinned it as latest, so nothing else rotted.
- **Commit-time ruff over staged Python** (`gates/py_staged.sh`, wired by
  `install.sh`): the cheap half of `py_gate` runs at commit time, so a
  lint finding surfaces one commit earlier instead of one push later.

## v0.12.1 — a named "not applicable" skip is not a pass _(2026-09-14)_

Patch. `check_empty_scope.py` counts a gate that exits 0 having printed
"not applicable" as a non-run, not as compliance over an empty tree. A
macOS-only gate on a Linux runner (monitor's codesign check) had no honest
place before: an excuse would be true on one host and false on the other,
and the stale-excuse ratchet fires either way. Probe case added; verified
on Linux in a container.

## v0.12.0 — the emoji gate reads escapes; the binary is measured end to end _(2026-09-14)_

Minor: a behaviour every consumer inherits on its next run.

- **Escaped codepoints are emoji too.** A Rust brace escape, a Python
  eight-digit escape or a JS four-digit escape that names a forbidden
  codepoint renders as one, and both checkers -- Python and native -- were
  blind to it (monitor's retired repo-local checker was not; that is where
  the class came from: two padlocks in a config panel). Both report the
  escape at the column of its backslash, marked "(written as an escape)",
  in the same order per line; parity and unit cases pin it. The first run
  over the fleet found 22 in CadGoose's UI strings (now the Kare set), and
  test fixtures in four repos that had been writing emoji as escapes to
  dodge the gate -- they are built from codepoint numbers now, which is
  neither a glyph nor an escape. Where an escape is legitimately the
  product (CadGoose's goose icon), `GOH_ALLOW` says so by name.
- **`crates/goh` is measured at 97.9% (floor 95, the house number).**
  `crates/goh/tests/cli.rs` drives the binary over fixture repos -- every
  subcommand, both scopes, every `.gatesrc` knob -- where `cargo llvm-cov`
  can see it; the Python parity suites had been giving it that exercise
  from pytest, where it could not.

## v0.11.1 — the Linux path, exercised _(2026-09-14)_

Patch: fixes to what v0.11.0 shipped, each found by a consumer's CI or by
running the release rules against this repo itself.

- **Portable `mktemp`.** `mktemp -t name` is BSD syntax; GNU mktemp needs an
  `XXXXXX` template, so the structural, manifest and swift gates died on the
  first Linux CI runs that called them by name. Fixed in one place; the
  claim of Linux support is now exercised by two consumers' CI.
- **`goh --version`**, checked by `scripts/build-goh.sh` against `Cargo.toml`
  and pinned by an integration test: an installed binary that cannot say
  what it is cannot be verified as the one just built.
- **Release kit: `--archive-build CMD` and `--verify CMD`.** Build from
  `git archive HEAD` (tracked files only -- what a tarball consumer gets)
  and install-and-exercise before the tag. ztools and sys_updater delegate
  to the kit again instead of carrying their own sequencing.
- `loc_of_baseline_files.py`: a sentinel-only baseline ratchets to a pass.

## v0.11.0 — the native gate is the gate; the house checks reach every repo _(2026-09-14)_

Minor: new behaviour every consumer inherits on its next run. What was WRONG
before, in the order a consumer would meet it:

### `.gatesrc` written as `export KEY=value` was invisible to the native binary

Three repos write their keys with the bash `export` idiom. The Python side
sources the file and saw them; the native parser keyed on the literal
`export GOH_EXCLUDE` and silently dropped the value -- ztools' vendored
crate passed under Python and failed under `goh`. The prefix is stripped;
proven red against the old parser.

### The no-`#[allow]` check ran only where a repo had copied it

`rust_gate.sh` looked for a repo-local `tools/check_no_allow.py` and warned
and skipped when absent. Four repos carried copies (one already behind);
every other Rust repo was never checked. The step now runs
`checks/check_no_allow.py` from here, unconditionally, and honours
`GOH_EXCLUDE` like every other house check (a vendored crate is exempt with
one line). The copies in antiknob, divoom-control, routines and ztools are
deleted.

### Length ceilings were required but never enforced here

`GOH_LINE_BASELINE` made the structural gate verify that every cap-exempt
file CARRIES a ceiling, and left the shrink-only ratchet to each repo's own
gate script -- a repo that listed ceilings and never wired the ratchet was
bounded by nothing. Both pipelines now run `check_baseline_ratchet.py` over
the baseline's files (`checks/loc_of_baseline_files.py` measures them; a
sentinel-only baseline ratchets to a pass). Proven red on one line of
growth, native and Python agreeing.

### Bin-only crates read as "no coverable lines"

`coverage_gate.sh` exported lib and test targets only, so a crate whose
unit tests live in its bin (`crates/goh` is one) could not be measured. Bin
targets are exported too; part files carry the target kind.

### The parity suites ran a binary that could vanish under them

Five module fixtures each built `goh` and executed it at its uplift path in
the machine-wide cargo target dir, where a concurrent relink (another xdist
worker, another repo) opens a window in which the path does not exist. One
session fixture builds once and hands out a private copy.

### This repo gates itself the way it gates everyone

`GOH_CI_STEPS` in `.gatesrc`: the house Rust gate over `crates/goh` with a
stated coverage floor (55%, the unit tests alone -- the parity suites drive
the binary where llvm-cov cannot see it), a biting `cargo audit`, the
structural gate, `cargo test`, and the pytest suite via `tools/pytest.sh`;
`tools/gate.sh --full` delegates to `local_ci.sh`. `clippy.toml` exempts
test code from the restriction lints, retiring five per-test `#[expect]`s;
the duplicate `tools/rust_gate.sh` is gone.

The rest of this release, from earlier in the cycle:

### The native `goh` binary carries layer 1

`gates/structural.sh` now execs `bin/goh` when `install.sh` has built it
(cargo present; `GOH_SKIP_BUILD=1` to skip) and runs the Python checkers,
saying so once, when it has not. `GOH_BIN` names a binary explicitly — a
pointer at nothing is reported, never silently replaced; `GOH_NO_NATIVE=1`
forces the Python path (the parity suites use it, so native is always
compared against Python, never against itself). `scripts/build-goh.sh` gates
the platform first (64-bit only, macOS Apple silicon only, Linux x86_64 and
aarch64 kept) and lands the binary atomically. Measured on ztools: 1.0 s
native against 1.5 s Python for the full structural gate.

### Full scope is the worktree

`listed_files` (both `checks/_gitutil.py` and `crates/goh/src/gitutil.rs`)
lists `git ls-files --cached --others --exclude-standard` for full runs:
tracked plus untracked-but-not-ignored. It was tracked-only, so a brand-new
oversized file was invisible to `ci.sh` / `--full` until it was staged —
twice in one day across two repos. Staged scope is unchanged (the index).
The empty-scope skeleton ignores its copied gate dir through
`.git/info/exclude` so the sweep stays blind to it; two excuses whose reasons
had stopped being true (`check_lints_optin.py` — this repo has had a Cargo
workspace since the `goh` crate landed — and `check_probes_pass.py`) are
deleted, as the ratchet demanded.

### Per-target Swift coverage floors, without the reroute

`check_swift_coverage.py` gains `--floors-json` (schema shared with
`coverage_gate.sh`), wired into `swift_gate.sh` as `GOH_SWIFT_COV_FLOORS`.
A package's overall percentage is dominated by whichever target has the most
lines -- on a SwiftUI app that is the views, which no unit test executes --
so the package number can sit above its floor while the target holding all
the logic rots.

A floors key is a path PREFIX relative to `Sources/`, matched segment-wise,
not only a target name. A single segment behaves exactly as a target name did,
so existing floors files are unaffected; a deeper key (`App/Core`) floors part
of one target. That is what makes it unnecessary to split a package into logic
and view TARGETS purely so a floor can be aimed -- a split that forces `public`
onto every type crossing the new module boundary. A directory is enough to aim
a floor at, and letting the coverage tool dictate module structure is the wrong
way round.

**A target named in a floors file that matches no measured source is now a
hard error in every implementation.** Both call sites in
`gates/coverage_swift.py` scored an unmatched target 100% and passed it, so
renaming or misspelling a target turned its floor into one that could never
fail. A floors file is the thing a reader trusts to say what is enforced; an
entry that enforces nothing has to be loud. A floors file that parses to no
floors at all is likewise a config error rather than an empty ratchet.

### The Swift coverage gate measured the wrong tree, with the wrong binary

Two defects in `gates/coverage_swift.py`, found while trying to route
`swift_gate.sh` through `coverage_gate.sh` so per-target floors would work.
The reroute is NOT done, because measuring the target first showed it would
have made every repo's Swift floor easier to pass.

**It could not measure an SPM package at all.** An `.xctest` bundle built
with debug symbols -- which is how `swift test` builds by default -- holds a
`.dSYM` DIRECTORY beside the executable in `Contents/MacOS`. The binary
selection took the first glob hit, handed llvm-cov the `.dSYM`, and llvm-cov
died with "Is a directory". The xcodebuild path had the same defect through
`pick_binary`. Both now select regular executable files only.

The existing selection tests missed it because their fixtures put the
executable in `Contents/MacOS` and nothing else. A harness that builds its
own inputs only ever tests the shapes it thought to build.

**And the failure named the wrong thing.** llvm-cov exiting non-zero was
reported as "could not parse llvm-cov JSON summary", with llvm-cov's own
stderr discarded -- so the one line that said `Is a directory` never reached
anyone. It now reports the exit code, the binary, and what llvm-cov said.

**Tests/ counted toward the floor.** `coverage_swift.py` applied no scope
filter, while `checks/check_swift_coverage.py` excludes test sources,
generated runners and DerivedSources. On the same tree (antiknob) the two
read 10.54% and 4.78%. A test file is ~100% covered by definition -- it is
the thing doing the running -- so a floor set on the first number can be met
by writing tests that assert nothing, which is the exact hole the checker
was fixed for on 2026-09-07.

One copy of a rule and one ABSENCE is worse than two copies: the second path
looked like it agreed and did not. The definition now lives in
`lib/swift_coverage_scope.py` and both import it, with a test that fails if
either re-grows a local copy -- and a behavioural test that the filter is
actually applied, because an earlier version of that guard asserted only
that the import was present and stayed green when the filter was deleted.

`gates/coverage_swift.py` passed the 500-line cap on the way; the
`cov:ignore` marker machinery moved to `gates/coverage_markers.py`.

## v0.10.0 — gates that reported clean about things they never looked at _(2026-09-07)_

Nineteen commits, one theme. Every gate below was GREEN over a subject it had
not read, and in each case success and vacuity printed the same word.

### The empty-scope campaign

**A gate that inspected nothing has not passed, it has abstained.**
`checks/check_empty_scope.py` runs every gate against a SKELETON TREE — the real
gate directory, the whole directory layout, no files — and fails any that reports
compliance over an empty population. Grepping for the guard was tried first and
was wrong in both directions: of 22 gates it accused three that were fine and
missed all three that were really blind, because the absence of a guard is not
visible in source. The scan is right, the comparison is right, and the
collection it iterates is simply empty.

Two harness details decide whether the sweep means anything: the gate directory
is COPIED so its baselines and allowlists come with it, and the copy is NOT
tracked — tracked, every whole-repo scanner finds the copied gates, does real
work on them and passes honestly, which reports as blindness.

Related fixes from the same sweep, each its own commit:

- **`_gitutil`: a failing git was reporting an empty repo, and every gate
  believed it.** One helper ran git without checking its exit code, so a corrupt
  index returned `[]` and every consumer printed "OK — 0 tracked files clean".
  Three "blind gates" were ONE defect.
- **`check_baseline_ratchet`: every entry vanishing is not every ceiling met.** A
  shrink-only ratchet has no ceiling left to exceed once its population reaches
  zero, so a total collapse read as the best possible result — one printed "24
  entries within ceilings" while NAMING all 24 as vanished, and exited 0. A
  populated baseline with an empty measurement now FAILS, and the success line
  reports what was MEASURED rather than what the baseline remembers. An empty
  baseline stays exempt, or the ratchet is unadoptable by a project starting
  from zero.
- **`check_tests_registered`: "all 0 test file(s) are registered" is not a pass.**
- **The self-proofs now RUN, because nothing ever did**, and a calibration
  registry records which shared gates have been PROVEN able to fail. Blindness
  and unprovenness are different defects: three gates in the best-scoring repo
  were 8/8 proven and still blind, because a probe shows a gate can fail on a
  VIOLATION and says nothing about an ABSENT subject.
- **`probes`: a `main(argv)` dispatch is a self-proof, and it was being missed.**

### Swift coverage counted the wrong sources

`fix(swift-cov)` twice: it reads `llvm-cov export` 3.x, and it stopped counting
TEST sources and GENERATED sources toward the coverage number. A floor computed
over the tests that are supposed to satisfy it measures nothing about the code.

### The disk watch left the repo, not just the gate

It had been moved out of CI while still living here; now it lives where it runs.

### The two ways a clippy gate lies

From a lint-adoption campaign across four Rust repos (monitor, routines,
ztools, divoom-control), all four taken from "no policy at all" to zero at
`-D warnings`. Two of the findings were structural rather than per-repo, which
is what made them belong here.

**`checks/check_lints_optin.py`** — a workspace's `[workspace.lints]` is a
DECLARATION; a member crate applies it with `[lints] workspace = true`, and one
that never says so inherits nothing. There is no warning anywhere: cargo does
not mention the omission and `clippy -- -D warnings` passes, because the crate
genuinely has no findings AT THE LEVELS IT IS SUBJECT TO. Measured on `monitor`:
the workspace had declared `pedantic` and `nursery` from the start, two of three
crates opted in, and the third — the biggest, the one uploaded to every
monitored host — had accumulated 254 findings that every green gate had agreed
were absent. Calibrated by removing that crate's opt-in and watching the check
go red. Six tests, including the empty-population case: a workspace with no
policy is NAMED as such rather than reported as a pass.

**`GOH_RUST_LINT_CONFIGS` in `gates/rust_gate.sh`** — the gate's clippy step
lints ONE cfg: this machine's target, all features on. A crate that is part
`cfg(target_os = ...)` or part `cfg(feature = ...)` has halves that command
never compiles and therefore cannot report on. On `monitor` that meant a Mac
checked one half and the ubuntu runner checked the other, neither failing on the
other's code and nothing comparing them: 26 findings on the host, 48 for
linux-musl, ten of them in src the host cannot see — plus two `#[expect]`s that
were UNFULFILLED there, which under `-D warnings` is an ERROR. The agent's build
was broken for the platform it ships to while every gate was green.

Each entry is extra cargo argv, `:`-separated. A `--target` whose std is not
installed is a HARD FAILURE naming the `rustup target add`, never a skip — a
step that inspects nothing must not read as a pass, which is this estate's
oldest gate defect. Calibrated both ways: with a Linux-only `#[must_use]`
removed, the existing clippy step stays GREEN and the new one goes RED.

**`gates/rust_manifest_gate.sh` remedy advice corrected.** It told the reader to
silence a finding with

    [lints.cargo]
    unused_dependencies = "allow"

which cargo does not support on stable — it is an "unused manifest key", so
cargo emits `(manifest) generated 1 warning`, which is precisely the string this
gate greps for and fails on. Following the gate's own advice made the gate fail.
Measured on cargo 1.98.1 with both `allow` and `deny`; the advice had never been
run. It now says to remove the dependency, explains why `[lints.cargo]` is a
trap, and points at `cargo-machete`, whose ignore list lives under a metadata
key cargo does read.

### An exemption from the CAP is not an exemption from every bound

**`checks/check_exclusion_has_ceiling.py`** — `GOH_LINE_EXCLUDE` and the
shrink-only ratchet are separate mechanisms with separate lists, and nothing
compared them. Found in `monitor`: a 619-line test file named in LINE_EXCLUDE
(so the cap did not apply) and absent from `loc_baseline.txt` (so no ceiling
applied either). Bounded by nothing — and the repo's baseline header asserted the
opposite in prose, which is why nobody looked. **A document that describes a
property nothing enforces is worse than silence, because it answers the question
that would otherwise get asked.**

Enabled per repo with `GOH_LINE_BASELINE`; a repo that sets LINE_EXCLUDE without
it is WARNED rather than passed silently. `GOH_LINE_UNBOUNDED` names the
exemptions that legitimately need no ceiling — captured vendor material that must
stay emoji- and secret-scanned but is not ours to split — because the first
version fired on a repo using the key correctly, and a warning on every commit
that the reader is meant to ignore is how a gate stops being read. Both lists are
checked for staleness: a waiver matching no tracked file FAILS.

Calibrated against the real hole: with the file reconstructed and exempted, the
cap says "OK — 302 files within 500 lines", the ratchet says "OK — 7 entries
within ceilings", and only this check exits 1.

Also extracts **`_gitutil.line_count`**. `check_file_length` had the convention
right (a trailing newline TERMINATES the last line) and the new check was written
with a plain `count + 1`, so at exactly 500 lines the two gates would have
disagreed about the same file — and a disagreement between gates reads as a
defect in the file.

### `cargo machete` — the unused-dependency gate that actually runs

`[lints.cargo] unused_dependencies = "deny"` reads like a gate and enforces
nothing: the namespace needs `-Zcargo-lints` on nightly, so on stable cargo
prints an "unused manifest key" line and exits 0. It had never been enforced on
any toolchain in the repo that declared it. `cargo-machete` does the job on
stable and exits 1 on findings, so `gates/rust_gate.sh` treats it as a hard
failure, with a NAMED skip when the tool is absent.

Calibrated on `routines`: clean, then an unused `heck` added and the step goes
red naming it, then clean again. Across the estate it found four real unused
dependencies (monitor, app_updates). Its blind spot is ident-based scanning, so
exemptions are local, in the crate's own `[package.metadata.cargo-machete]`,
with the reason.

### `check_skills_corpus.py` — and then a caller for it

A skills corpus is authored prose that nothing compiles, so its defects are
silent. Six checks: frontmatter, name/directory agreement, `[[wikilink]]`
resolution, reference-link resolution, a word ceiling, and duplicate
lesson-shaped titles. Each found a real defect on its first run.

It then had **no caller** — no repo set `GOH_SKILLS_CORPUS`, so the gate written
to catch silent defects was itself running nowhere, which is the class it exists
for. Wired into this repo's own `.gatesrc`, because `~/.claude/skills` is a
global asset with no repo of its own.

**An external corpus that is ABSENT is a named skip, not a hard failure.** The
first wiring hard-failed wherever `HOME` is not the developer's — which is exactly
what this repo's own self-host test does, committing a copy with `HOME` set to a
temp dir to prove `.gatesrc` stays hermetic. The test was right. Calibrated all
three branches: present and broken exits 1, present and clean exits 0, absent
warns and exits 0.

## v0.9.0 — eval findings 1-10 _(2026-09-04)_

Every item from EVAL-2026-09-04.md, fixed test-first, measured throughout.

**What shipped**:
- Layer 1 shell lint: `checks/check_shell_lint.sh` (`bash -n` always;
  `shellcheck --severity=error`, missing binary degrades to a named
  warning). Wired into `structural.sh` both scopes. Found two live bugs
  on adoption (a prose `# shellcheck ...` comment parsed as a directive;
  a sourced lib missing its shell directive) — both pinned by tests.
  shfmt deliberately excluded (tree uses aligned continuations).
- Rust coverage floor: `rust_gate.sh` runs `coverage_gate.sh --lang rust`
  when `GOH_COV_FLOOR_RUST`/`GOH_COV_FLOORS_JSON` is set (explicit argv —
  `.gatesrc` values are not exported, found by the red test), warn nudges
  otherwise like py/swift gates.
- Secrets gate: `checks/check_no_secrets.py` (known prefixes + key
  headers, staged + full, `secret-ok: <reason>` markers; bare markers
  suppress nothing). No entropy heuristics by design. Tree scans clean.
- Swift parity, not merge: `tests/test_swift_coverage_parity.py` runs the
  legacy helper and the llvm-cov engine against one 50% fixture and pins
  identical verdicts (red-proven). A merge needs a real-swift oracle that
  does not exist here.
- `docs/contracts.md` #11: `.gatesrc`-as-shell trust posture recorded.
- UX: `GOH_LCI_TIMEOUT` per-step ceiling for `local_ci.sh` (TERM/KILL +
  subtree sweep, exit 124, non-numeric is exit 2); `--help` for
  `install.sh`, `tools/gate.sh`, `structural.sh`; `gates/doctor.sh`
  (wiring diagnosis: GOH resolution, hooksPath, unknown `.gatesrc` keys
  derived from `docs/config.md`, toolchain presence) + `--doctor` in
  both gate entries.
- `GOH_TIME=1`: per-step elapsed seconds on ok lines (`_common.sh`).
- `docs/BACKLOG.md`: the rule-#13 file, seeded with deferred items.
- `slow` marker: the 28s real-swiftlint test opts out of the fast loop
  (`-m "not slow"`); the gate still runs everything.
- `lib/mcp_schema.py` split out of `mcp_scaffold.py` (496 → 422/113
  lines); block moved byte-identically, re-exported so downstream
  imports keep working; ruff hits fewer than HEAD (5 → 4).
- Trim: fake-gh stub deduped to `conftest.py`; dup import pruned; stale
  `ci_local`/`loadfile`/count references fixed across docs.

**Bugs the work itself caught**:
- `X | Y` annotations crash OS python3 3.9 at def time (the documented
  class, reintroduced in two new files) — caught by the self-host
  dogfood test; fixed with `from __future__ import annotations` plus a
  one-line class fix in `check_disk_hygiene.py`.
- Parallel-suite race, take two: release-hardening's mid-run edit vs
  release runs (fixed by xdist grouping, contract #10); my own
  mutate/restore red-proof raced `__pycache__` (sleep-separated rerun).

**What deliberately didn't ship**:
- Entropy-based secret detection, shfmt enforcement, swift merge,
  launchd schedule for the disk watch (all in `docs/BACKLOG.md` with
  unblock conditions).

## v0.8.0 — disk watch out of CI, pooled du, parallel suite _(2026-09-04)_

Measured first: full `structural.sh` was 17.7s, of which `check_disk_hygiene`
was 15.1s (a du stat-storm: $TMPDIR 7.4s + 37GB shared cargo-target 4.1s +
project targets 1.5s). The checkers themselves total 0.45s — a Rust rewrite
would speed up the wrong 3%.

**What shipped**:
- Disk hygiene OUT of the gate: `structural.sh` no longer runs it on any
  scope (full tree now ~0.5s, was ~17.7s). Pinned by
  `test_no_gate_runs_disk_hygiene` — re-adding a gate reference fails red.
- The watch lives on as `~/Projects/scripts/bin/disk_hygiene.sh` (thin
  wrapper, same delegation via `GOH_DIR`), unified with
  `reclaim_build_space.sh`: a failure names the reclaimer as the fix.
  Covered by `scripts/tests/test_disk_hygiene.sh` (both directions).
- `check_disk_hygiene.py`: one ThreadPoolExecutor over ALL roots (two pools
  still stacked the slowest scratch root on the slowest cache dir — 12.4s;
  one pool: 15.1s → ~8s) + `GOH_SCRATCH_ROOTS` seam for scoping/tests.
- Parallel suite: `tools/gate.sh --full` uses `pytest -n 8 --dist loadgroup`
  when xdist is installed (loadgroup keeps xdist-grouped files on one
  worker; ungrouped files spread by load), serial + warn otherwise.
- Trim: the 36-line fake-gh stub duplicated in both release suites now lives
  once in `tests/conftest.py` (`test_release_kit.py` 497 → 456 lines);
  pruned a duplicate `import pytest`; fixed stale `ci_local.sh` name and
  `loadfile` references across docs.

**What deliberately didn't ship**:
- No Rust rewrite (evidence says it buys nothing here).
- No launchd schedule for the watch (manual/periodic by hand for now).

**Tests**: 3 new (wiring removal-pin + config-schema seam coverage via
existing tests + scripts wrapper suite); 442 pass in ~40s parallel
(`-n 8 --dist loadgroup`), was 441 in ~168s serial. Baseline gate: full
`structural.sh` 17.7s → 0.5s; standalone watch ~8s (was 15.1s in-gate).

## v0.7.0 — LLM-navigability pass _(2026-09-04)_

Everything from the review of what slows an LLM down here, fixed
additively — no gate behavior changed, no key renamed.

**What shipped**:
- `AGENTS.md`: agent entry point (commands, layout, rules, config pointer).
- `docs/map.md`: every gate/checker/lib/tool with purpose + pinning test;
  coverage-file overlap (`coverage_gate.sh` vs `coverage_swift.py` vs
  `check_swift_coverage.py` vs `lcov_merge.py`) resolved in prose.
- `docs/config.md` + `.gatesrc.example`: single schema for all ~30 `GOH_*`
  keys (README documented ~10); defaults, readers, and the
  `GOH_EXCLUDE` ∪ `GOH_LINE_EXCLUDE` union rule.
- `tests/test_config_schema.py`: drift gate — a new `GOH_*` key without a
  `docs/config.md` row goes red (red-proven with a synthetic key).
- `docs/contracts.md`: nine load-bearing invariants, each naming its test.
- `docs/new-checker.md`: test-first contributor cookbook (fixture helpers,
  `chr(0x...)` rule, staged/index testing, wiring + schema obligations).
- Cutover: `README.md` points at the new docs; `install.sh` starter
  `.gatesrc` points at `docs/config.md` / `.gatesrc.example`.

**What deliberately didn't ship**:
- No `GOH_*` renames, no coverage-file merges, no hook behavior changes.

**Tests**: 2 new in `tests/test_config_schema.py`; 441 total pass.
Baseline before the pass: 439 pass.

## v0.6.0 — coverage strictest convergence, disk cargo-cache watch, MCP Tax parity _(2026-08-26)_

Convergence to the strictest coverage standard across toolchains, third-class
disk leak monitoring, and MCP Tax parity. 399 → 439 tests.

Coverage convergence to strictest standard:
- `coverage_gate.sh`: positive inclusion filtering before ignore (`--include` /
  `GOH_COV_INCLUDE_RE`), per-target and per-file floors with tolerance/exempt
  rules (`--floors-json` / `GOH_COV_FLOORS_JSON`), and shrink-only forgiven-lines
  ceiling (`--marker-ceiling` / `GOH_COV_MARKER_CEILING`). Backward-compatible
  with single `--floor`.
- `coverage_swift.py` & `lcov_merge.py`: include filtering for llvm-cov export /
  parse_xccov, multi-shape floor config loading, per-file floor enforcement with
  stale-exempt detection, and shrink-only marker ceilings.

Disk hygiene, local CI & release kit:
- `check_disk_hygiene.py`: watches shared `CARGO_TARGET_DIR` (`~/.cache/cargo-target`)
  and per-project targets for unbounded cache growth (`--max-cache-dir-gb` /
  `GOH_MAX_CACHE_GB`, default 50 GB) with largest-child diagnostics.
- `local_ci.sh`: preserves failing step log directories on exit so
  reproducibility seeds printed at top of output are retained.
- `release.sh`: unsets `GOH_RELEASE_BUFFERED` after startup snapshot so nested
  invocations and gate test runs remain hermetic and self-buffering.

MCP scaffold & eval transport:
- `lib/mcp_scaffold.py`: Tax SDK parity with server instructions support, input
  schema validation before tool handlers (`-32602 INVALID_PARAMS`), protocol
  version negotiation (`HANDSHAKE_VERSIONS` / `LATEST`), and `tool_from_function`
  helper. Server-death crash class closed and JSON-RPC conformance pinned.
- `lib/eval_transport.py`: Ollama environment variable aliasing (`OLLAMA_API_BASE` /
  `OLLAMA_HOST`, `OLLAMA_MODEL`) as fallbacks behind EVAL / OPENAI keys.

## v0.5.0 — second adversarial pass _(2026-08-25)_

Re-review of v0.4.0 by three fresh hunters (fixes-as-hostile-code, oracle
fuzzing, untouched surfaces) plus refute-first verification. 399 tests.

Fail-open closed:
- `goh_init`'s EXIT trap erased crash exit codes — a gate with a syntax error
  exited 0 and a real consumer commit shipped silently green. Exit 0 is now
  honored only after the completion sentinel; any other path re-raises.
- `coverage_gate` laundering hole: a failing export that left its output file
  behind passed completeness ("100%" over garbage). Success now requires
  per-target `.ok` markers; the redundant count check is gone.
- `py_gate`'s bare `--cov` floored only modules the tests happened to import —
  a never-imported module at 0% passed `GOH_PY_COV_MIN=100`. Coverage is now
  scoped to the package.

Fuzz-proven hardening (oracle oracles, thousands of seeded cases):
- PNG fallback decoder: all corrupt-input escapes are named preconditions
  (were raw tracebacks); **zero wrong-pixel decodes across 12k corruptions** —
  the load-bearing property, now pinned.
- lcov merger extracted to `gates/lcov_merge.py`: BOM'd part files no longer
  silently drop their first record (a real 75% showed as green 100%); FN
  format detected by content; malformed records exit 2 naming file:line.
- Baseline ratchet rejects huge-int JSON (OverflowError traceback) and
  liberal numeric literals in line baselines.
- `local_ci` steps run from the repo root; stdin isolation regression-pinned.

Also: screen-check line lists derive from `\n` (control characters crashed
the probe); `check_no_allow` depth-counter scan fixes an FP and a found FN;
coverage_swift pairs profdata with mtime-nearest binary and refuses ambiguity;
wiring meta-gate parses guarded lines (typo'd refs can't ship blind);
NO_COLOR honors the spec; styled TUI output routes warnings to stderr with
data-safe printf; multiple xcodeproj candidates die instead of locale-picking;
emoji ranges extended (geometric shapes, astral forward-compat) with zero
blast radius across consumers. Consumer forks (divoom ×2, monitor,
koffee_big) got the round-one `-z` fix too.

## v0.4.0 — adversarial review: every finding fixed _(2026-08-25)_

Three independent hunters + refute-first verifiers over checks, shell gates and
consumer seams; 19 confirmed findings, all fixed with regression tests built
from their repros. 289 → 355 tests.

Gate-bypasses closed:
- `_gitutil` now lists paths NUL-delimited — quoted non-ASCII filenames were
  silently skipped by EVERY checker in staged AND full mode.
- `local_ci` steps no longer inherit the loop's stdin — a stdin-reading step
  swallowed all remaining steps and reported "all passed" exit 0.
- `coverage_gate`: a per-target lcov export that fails is a hard failure naming
  the target (rust + cpp) instead of a warn over partial measurement.
- `check_swift_coverage` SPM mode refuses payloads that parse to zero
  measurable files (was: "100% OK", exit 0).
- golden tolerances and ratchet values reject NaN/Infinity (NaN defeated both
  in every direction); non-finite is a named precondition, never a verdict.
- `check_no_emoji` adds the singleton emoji codepoints outside scanned ranges;
  ©®™ documented as permitted typography — bare forms pass, VS16 presentation
  still fails.

False positives removed:
- `check_no_screen_presentation`: single left-to-right state scanner — `//`
  inside a string no longer blanks real code later on the line, and `/*` in a
  string can't open a fake block span.
- `check_no_allow` ignores comment mentions of `#[allow]` (the prose a cleanup
  PR writes), still catches real attributes.
- `coverage_gate` merger parses both lcov FN formats; three-field spans bound
  by parsed end (two-field legacy behavior pinned byte-identically — moving it
  would shift consumer coverage numbers).

Half-states / hardening:
- `release.sh`: stale-tag at an older commit hard-fails naming both SHAs;
  awk stanza matching no longer eats regex backslashes via `-v`; artifact
  fetch failures name the step.
- `install.sh` records installed-hook hashes — re-running bootstrap no longer
  clobbers hand-extended hooks; `--force` overrides.
- `mcp_scaffold` returns isError for unserializable tool results instead of
  dying mid-session; subprocess timeouts kill whole process groups
  (`lib/killtree.py`); disk hygiene measures through partial du failures and
  checks free space per scratch root; local_ci runs steps from the repo root;
  profiling scripts carry line-1 shebangs.
- Self-host: GOH's own `--full` now runs its test suite — a gate bug can no
  longer reach eleven consumers without failing here first. README documents
  `git commit --no-verify` as the escape hatch.

## v0.3.0 — post-unification hardening _(2026-08-25)_

- `checks/check_no_screen_presentation.py`: bare-identifier matches in Swift
  TYPE position (annotations, params, returns, casts, generics) no longer
  flag; use positions (`NSScreen.main`, `.screens`) still do. Erases the
  fake-protocol-surface markers ZoneTilerWM reported.
- `tools/release-kit/release.sh` hardening: `--no-push` implies skipping the
  GitHub-release step; the script self-buffers at startup so a concurrent
  edit can no longer corrupt a running invocation (field-reported as a silent
  exit 0 — worse than a crash); stanza extraction keeps `###` subsections and
  tags carry them via `--cleanup=verbatim`; X.Y versions and the
  missing-CHANGELOG guard are pinned by regression tests.
- `gates/swift_gate.sh` + `gates/swift_lint_baseline.py`: `GOH_SWIFT_LINT_BASELINE`
  turns the lint stage into a shrink-only ratchet — baselined violations tolerated,
  NEW ones fail named, vanished ones are a re-record nudge. Match key (file, rule,
  reason) probed against swiftlint 0.65.1: code motion and severity flips stay
  tolerated, reason drift and other-file twins do not. Unset keeps bare strict
  linting. Red-proven both directions.

## v0.2.0 — the harness unification _(2026-08-25)_

Maximalist centralization: sixteen per-repo harness families moved under one
roof. 74 → 262 tests, every new checker red-proven.

- `lib/desktop_lock/`: canonical machine-wide desktop mutex relocated from
  `~/Projects/scripts/lib` (PID+start-time record contract preserved verbatim).
- `checks/check_baseline_ratchet.py`: shrink-only ceilings (JSON or line
  baselines) replacing monitor/necrohand/ZeroThunder ad-hoc ratchets.
- `checks/check_generated_fresh.py`: artifact-freshness gate (regenerate to a
  temp sandbox, hash-compare, timeouts mandatory) — the Taxes pattern,
  generalized.
- `checks/check_no_screen_presentation.py` + `lib/headless_env.sh` +
  `checks/check_no_screen_linkage.sh`: the two-halves screen invariant —
  static grep (absorbed necrohand's full pattern set, differential 3/13 →
  14/14), runtime env contract, and an nm -u link-table audit.
- `checks/check_tests_registered.py`: every test source on disk must be
  registered inside an add_executable/add_test block (stronger than the
  CadGoose/CadGoose2 verbatim twins it replaces).
- `lib/golden_core.py`: shared pixel-diff core (mean abs
  diff, changed fraction, SSIM; Pillow-or-pure-Python); blessing stays repo
  policy by design. Offscreen-render cookbook recorded in `docs/harnesses.md`.
- `lib/eval_transport.py` + `lib/mcp_scaffold.py`: grader/model-agnostic eval
  transport (parse-rate guardrail, atomically resumable sweeps) and stdio
  MCP JSON-RPC scaffold matching the newline-delimited framing all four
  ancestor servers speak.
- `tools/profiling/`: the CadGoose soak/profile harness canonized (the two
  repos carried byte-identical copies); target-root seam so neither repo
  profiles the wrong checkout.
- Migrations shipped across eleven consumer repos (app_updates, monitor,
  divoom-control, CadGoose, CadGoose2, sys_updater, routines, necrohand,
  koffee_big, ZeroThunder, ZoneTilerWM): forked length checks deleted,
  coverage/local-CI delegating with step lists diffed old-vs-new, locks and
  ratchets on the shared implementations. Honest keeps documented where a
  local contract was strictly stronger.
- Class fixes found BY the unification: cargo target enumeration via
  find(1) silently lost directory-style test suites (now cargo metadata);
  cpp coverage drove display-taking tests on the user's desktop (now honors
  GOH_CTEST_ARGS); release-kit stanza extraction truncated at ### headings;
  update_dev quit-detection no-op and signature clobbering (PROCESS_NAME,
  verify-then-adhoc).
- `tools/release-kit/`: ONE parameterized releaser (`release.sh` — gate →
  changelog stanza → idempotent annotated tag → push → gh release → Homebrew
  tap bump; every step skippable, every failure names its step, `--dry-run`
  prints without executing), one Apple icon pipeline (`gen_app_icons.py` —
  sips normalize → fixed ten-member ladder → iconutil .icns + optional modern/
  legacy appiconset), and a templated dev installer (`update_dev.sh` — the
  union of the koffee/necrohand/routines copies: quit-before-replace, ditto,
  absolute-path xattr with post-clear verification). Covered by
  `tests/test_release_kit.py` against real git + local bare remotes and a
  stateful fake gh.
- `gates/coverage_gate.sh`: ONE parameterized coverage gate
  (`--lang rust|swift|cpp|py --floor N [--ignore RE] [path]`) replacing six
  per-repo copies that drifted. Rust mode ports the app_updates implementation
  (per-test-target lcov exports + CGU-hash normalization, exact uncovered-line
  reporting); swift/cpp/py adapt necrohand+ZeroThunder, CadGoose2 and
  sys_updater respectively. Floors resolve `--floor` → `GOH_COV_FLOOR_<LANG>`
  → named exit-2 failure; exclusion regexes pass through verbatim, never
  invented. Real cargo llvm-cov e2e test (skipped when the toolchain is absent).
- `gates/local_ci.sh`: ONE declarative step runner for the ~10 copy-pasted
  local-CI orchestrators — steps from `.gatesrc` `GOH_CI_STEPS` (colon-
  separated) and/or `--step` args, tui-styled steps, logs captured to a temp
  dir and dumped on failure, fail accumulator (a failing step never stops the
  run), `--dry-run`, nonzero exit iff any step failed.
- Post-unification adoptions: necrohand/koffee_big/ZeroThunder MCP servers
  ported onto `lib/mcp_scaffold.py` under characterization-pinned wire parity
  (Taxes keeps the official SDK — it provides validation and surface the
  scaffold does not); ZeroThunder's golden suite delegates its pixel math to
  `lib/golden_core.py` (bit-exact parity proven over the baseline corpus);
  ZoneTilerWM's ui sweep likewise (exactly equal changed-pixel counts on all
  70 surfaces) and enabled the swift gate over its lint baseline, fixing a
  pre-existing actor-isolation error for real.

## v0.1.1 _(2026-08-25)_

- License: MIT.
- Policy: permit functional Mac key glyphs (`← ⌘ ⌥ ⌨ ⇧ ⌃ ⏎ ⎋ ↵`) and the text
  operators `⇒ ⇄`; per-repo extras via `GOH_ALLOW` in `.gatesrc`.
- Hygiene: untrack `.opencode` session state; ignore it.

## Gate wiring, harness, and class fixes _(2026-08-24)_

First full audit of the factored-out gate suite found three broken wirings,
two self-violations of the repo's own thesis, and several stated-vs-implemented
drifts. Everything was fixed test-first: the Phase-0 harness was written
against the broken tree and its 10 red failures each mapped to one finding.

**What shipped**:

- `tests/` harness: fixture-repo builder, per-checker contract tests, the
  wiring meta-gate (`tests/test_wiring.py` — parses every gate/hook for
  referenced scripts and asserts they exist), e2e `structural.sh --staged`
  runs, and real-`git commit` hook tests through `core.hooksPath`.
- `checks/check_swift_coverage.py` — shipped; both swift_gate modes called it
  and it never existed. SPM codecov JSON + xcresulttree walk; missing payloads
  are exit 2 with a named reason, never a fake 0%.
- `install.sh` + delegation hooks — hooks resolve the shared checkout via
  `GOH_DIR` (default `~/Projects/gates_of_heck`) so there is exactly one copy
  of every checker; pre-commit runs structural `--staged`; pre-push names the
  missing `tools/gate.sh` instead of exec-failing. This repo is self-hosted.
- `tui/lib.sh` publishes `_lib_*` names via private `_tui_*` impls — the
  styled lib could never engage before (the `_lib_info` probe was always
  false). Naive alias-to-public-name first attempt recursed to a bash
  segfault; caught by the new harness before commit.
- `gates/rust_gate.sh` un-forked onto `_common.sh` (~20 lines); parity proven
  against the preserved legacy fork (`tests/fixtures/rust_gate_legacy.sh`) on
  real crates across six scenarios. Added `goh_step_in`: in-dir steps as pure
  argv, no shell-string interpolation.
- Class fixes: `checks/_gitutil.py` makes ALL staged checks measure the index
  (`git show :path`), not the worktree; `EXCLUDE_PREFIXES` replaced by
  `--exclude` regex from `GOH_EXCLUDE`; `.gatesrc` and vendored tui resolved
  from the git root (subdirectory-safe).
- Stated-vs-implemented: `@generated` window is now the documented "first 40
  lines"; `GOH_SWIFT_COLD` defaults to 1 as claimed and xcode mode honors it
  by wiping the pinned `.build/xcode-dd`; emoji failure message generated from
  the `ALLOWED_ORDERED` constant (was hand-copied, omitting ← ⌘ ⌥ ⌨).

**What deliberately didn't ship**: `cpp_gate.sh` / `kotlin_gate.sh` (no
consumer yet — graduate on third use); CI integration (local-only by design);
disk-check top-5 truncation (documented tradeoff).

**Caught by the gates during this work**: a literal U+21D2 in a test comment
blocked by the freshly installed pre-commit hook (dogfood working as intended);
OS python3 3.9 crashing on `bytes | None` annotations at def time, found by
the self-host test.

**Tests**: 73 new across `tests/`; suite is 70 tests + 6 rust parity tests,
all green; red-proof receipts recorded in test docstrings.
