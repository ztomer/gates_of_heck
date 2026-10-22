# docs/contracts.md — load-bearing invariants

Each contract names its pinning test. Do not "fix" a contract without
updating its test and this doc together.

## 1. Fail-fast with output (`gates/_common.sh:102-114`)

`goh_step` runs the command captured; on failure it dumps the tail
(`GOH_TAIL`, default 60) and exits nonzero. A gate never keeps going
after a failure and never prints a bare label without the output.
Pin: `tests/test_gates_e2e.py` (failing step exits nonzero and shows output).

## 2. Completion sentinel (`gates/_common.sh:66-100,142-146`)

`goh_init` owns the EXIT trap. Exit 0 is honored only if `goh_done` ran
(`GOH_COMPLETED=1`); a 0 arriving without completion (parse error, crash,
syntax error mid-file) is re-raised as failure. A second `goh_init` must
not orphan the first log (`GOH_LOGS` accumulates).
Pin: `tests/test_goh_init_trap.py`.

## 3. Index truth (`checks/_gitutil.py`)

Staged (`--staged`) checks measure the git index via `git show :path`
(NUL-delimited paths, so quoted non-ASCII names are not skipped), never
the worktree. Full runs measure tracked worktree files.
Pin: `tests/test_gitutil_paths.py` (+ staged e2e in `test_gates_e2e.py`).

## 4. Argv stays argv (`gates/_common.sh:127-139`)

`goh_step_in <dir> <label> <cmd...>` runs inside `<dir>` without
shell-string interpolation (`env bash -c 'cd "$1" && exec "${@:2}"'`).
Paths with spaces or quotes stay data. `GOH_PY_RUNNER` is the one
stated exception: whitespace-split, so a runner path containing a
space cannot be expressed (use `.venv/bin/python`).
Pin: `tests/test_cwd_and_py_gate.py`, `tests/test_rust_gate.py`.

## 5. Headless tiers (`lib/headless_env.sh`, `docs/harnesses.md`)

`GOH_HEADLESS=1` (truthy) enforces offscreen policy. `headless_enforced`
reports it; `headless_require_live <name>` refuses by EXITING
`$GOH_HEADLESS_REFUSAL_EXIT` (default 3, distinct from 1 = ran and
failed). Attended runs opt out with `env -u GOH_HEADLESS`. GUI programs
refuse themselves; harnesses forward with `env $(headless_env)`.
Pin: `tests/test_screen_presentation.py` (truthy/falsey matrix + refusal code).

## 6. Golden compares, never blesses (`lib/golden_core.py`)

`golden_core.py` owns comparison only (metrics vs explicit tolerances;
exit 0 within / 1 exceeded / 2 precondition). There is deliberately NO
`--update`: blessing stays repo policy (ZeroThunder `golden.py --update`,
ZoneTilerWM `ui_regression_sweep.py --record`). Defaults are starting
points calibrated per repo on its measured noise floor.
Pin: `tests/test_golden_core.py` and `tests/test_golden_core_cli.py`.

## 7. Coverage floors are stated or refused (`gates/coverage_gate.sh:25-29`)

Floor resolution: `--floor`, then `GOH_COV_FLOOR_<LANG>`, else exit 2
naming both seams. A gate without a floor never silently passes. Failed
exports must not leave output files that read as success (per-target
`.ok` markers required). Bare `--cov` (modules merely imported) is
rejected in favor of package-scoped measurement.
Pin: `tests/test_coverage_gate.py`, `tests/test_cwd_and_py_gate.py`.

## 8. One checker, one checkout (`hooks/pre-commit`, `hooks/pre-push`, `install.sh`)

Hooks delegate to the shared checkout via `GOH_DIR` and vendor nothing.
`install.sh` records installed-hook hashes and refuses to clobber a
locally modified hook without `--force`; it writes starter
`tools/gate.sh` / `.gatesrc` only when absent. It is all-or-nothing:
every refusal (each modified hook named) and a failed `bin/goh` build
come before the first write, so a refused install leaves the target
byte-for-byte unchanged. `pre-push` names a missing `tools/gate.sh`
instead of exec-failing.
Pin: `tests/test_install.py`, `tests/test_install_atomic.py`, `tests/test_wiring.py`.

## 9. Config schema cannot drift (`docs/config.md`, `.gatesrc.example`)

Every `GOH_*` key read in `gates/ checks/ lib/ tools/ hooks/` appears
in `docs/config.md`; `.gatesrc.example` invents none. Adding a key
without documenting it fails the suite.
Pin: `tests/test_config_schema.py`.

## 10. Parallel suite shares nothing mutable (`tools/gate.sh --full`)
`--full` runs `tools/pytest.sh` (`pytest -n 12 --dist loadgroup`). Files sharing an
`xdist_group` marker stay on ONE worker: `test_release_hardening.py`
corrupts `tools/release-kit/release.sh` MID-RUN on purpose, and
`test_release_kit.py` runs real releases — split across workers, the
latter reads corrupted bytes and dies with a syntax error (found
2026-09-04 the first time the suite ever ran parallel). `test_desktop_lock`
has its own group: its tests take the REAL machine-wide mutex, so
splitting them across workers means contending with themselves.
Pin: the full suite green under `-n 12 --dist loadgroup`; serial green
proves nothing about the grouping.

## 11. `.gatesrc` runs as shell — trusted repos only

`structural.sh`, `rust_gate.sh`, `swift_gate.sh`, `py_gate.sh` and
`local_ci.sh` all source the target repo's `.gatesrc` into the gate
shell, so a malicious `.gatesrc` in a cloned repo executes on gate run.
Blast radius today is ~zero (every consumer is the operator's own repo —
the same trust already extended to hooks and `tools/gate.sh`), which is
why this is a documented posture, not a ticket. If gates ever run
against untrusted checkouts, replace sourcing with a KV parser first.
Pin: this paragraph (no test can prove a negative trust boundary).

## 12. A hook's repository variables never reach a FOREIGN repo's git

A hook exports `GIT_DIR`/`GIT_INDEX_FILE`; under a linked worktree
`GIT_DIR` is absolute, so an inheriting `git init <tmp>` re-initialises
the REAL repo and writes `core.bare = true` into its shared config
(zinc, 2026-09-27). Every git call on a skeleton, fixture or export —
and every process run inside one — drops git's own list
(`git rev-parse --local-env-vars`): `_gitutil.foreign_repo_env` in
Python (`check_empty_scope`, `check_probes_pass`), `goh_unbind_git`
(`gates/_git_env.sh`, the ONE shell helper) in shell,
`goh_testkit::git_command`/`git_in`/`goh_at` in Rust, and
`tests/conftest.py` at import.

The HOOKS drop them at entry, before anything is spawned
(`goh_hook_unbind`: `hooks/pre-commit`, `gates/push_gate.sh`): a
consumer's own test suite, run by its `tools/gate.sh --staged`, made a
scratch repo and wrote `core.bare=true`, a commit and three stashes into
the committing repository (media_server, 2026-10-08). The repository is
found again from the hook's cwd (refused, named, when it cannot be). The
index being committed is NOT the repository's own under `commit -a` and
`commit <paths>`; it travels as `GOH_HOOK_INDEX_FILE` with its git dir,
and a reader of the staged tree (`structural.sh --staged`, `goh.sh`,
`py_staged.sh`, `goh_index_view`, the proven key) binds it again with
`goh_bind_hook_index` -- only inside that repository. Nothing else reads
the carried pair: a consumer gate that did (`GIT_INDEX_FILE="${GOH_HOOK_INDEX_FILE:-...}"`)
bound it inside a TEST's fixture and judged the outer index (routines,
media_server, 2026-10-08), so `goh bare-hook-index` refuses an expansion of
either variable in any shell source but the one defining the binder.
Pin: `tests/test_commit_hook_git_env.py`, `tests/test_hook_git_env.py`,
`crates/goh-testkit/tests/hook_git_env.rs`, `crates/goh/src/hookindex/tests.rs`.

## 13. A name is checked against the object it names, never the working tree

`check_tag_version.py` reads a pushed `refs/tags/v<semver>`'s version with
`git show <commit>:<path>` at the commit the tag resolves to (`^{commit}`, so an
annotated tag peels), never from the checkout. It reads the refs git hands the
hook on stdin, never every tag in the repo, and it runs in `push_gate.sh`
BEFORE the two skips that would otherwise exempt a retag of a commit the remote
already holds.

The class: a name that makes a claim, checked against something other than the
object it names. media_server, 2026-10-01 — two `--amend --no-edit` runs
rejected by pre-commit under `2>/dev/null`, then `git tag -f v1.79.3` on a
commit still declaring 1.79.1, published by `--follow-tags`. The commit gate
proves the commit builds and passes; it can never prove the name is true.
Pin: `tests/test_check_tag_version.py` (red on the real shape, green on a
matching pair, a dirty working tree does not rescue it, an unrelated stale tag
does not block an unrelated push) and `tests/test_push_gate.py`.

The same name has a second claim: that it is PUBLISHED. With the hook's remote
(`--remote`, passed by `push_gate.sh`), a local `v<semver>` tag reachable from a
pushed branch must be in that push or already on the remote, asked with one
`git ls-remote` (a remote that cannot be asked refuses, never passes). The
servers fleet, 2026-10-10: `git tag v1.10.3` (lightweight) and
`git push --follow-tags`, which sends annotated tags only, so main moved and two
hosts described themselves as `v1.10.2-2-g…`. The rule is about the tag, never
the bump: a version may reach the branch before its tag exists (monitor and
antiknob tag after CI). Pin: `tests/test_tag_version_unpushed.py` and
`test_check_tag_version_push_gate.py`; `tools/release-kit/release.sh` sends the
branch and its tag in ONE push (`tests/test_release_kit.py`).

## 14. A committed artifact that makes a claim is checked against its source

`Cargo.lock` states each workspace crate's version; `Cargo.toml` asks for one.
They are the same claim written twice, and `goh lock-version` compares
them. Two rules make that comparison safe rather than noisy:

* **`version.workspace = true` is an INHERITANCE, not a version.** It resolves
  to `[workspace.package] version` first. Compared as text it is the string
  `true` against `1.36.0`, which invents a finding on every member of a modern
  workspace and is right about none of them.
* **A `cargo:` release source is SELF-REFERENTIAL and is not used for the
  release arm.** It yields the workspace's own number, so comparing crates
  against it is a repo disagreeing with itself — `monitor` ships
  `multitop-vault` at 0.21.0 under `[workspace.package] version = "0.51.0"`,
  deliberately. The release arm reads only a source OUTSIDE the manifests
  (a `VERSION` file), where a disagreement is about the release rather than
  about resolution.

The defect, app_updates 2026-10-01: a release commit bumped
`[workspace.package] version` to 1.36.0 and shipped `Cargo.lock` still saying
1.35.0 for all four crates. Nothing noticed because any `cargo build` silently
rewrites the lockfile — so the working tree heals on the next compile while the
COMMIT, which is what gets published, stays wrong. `check_tag_version.py` reads
`Cargo.toml` and never opens the lockfile: the same class as contract 13, one
file over.

Pin: `tests/test_check_lock_version.py` (the real shape is red; a regenerated
lockfile is green; per-crate versions produce no finding; a release number
bumped in one place only is red), run by this repo's push gate against the
native `goh lock-version`.

## 15. The `--version` FORMAT is a contract, and the checker reads the CLAIM not the string

A binary that answers "which version?" and cannot answer "which build?" is
trusted and wrong. The rendered format is a contract:

    <name> <crate-version> (<HEAD-short><+dirty> built <YYYY-MM-DD>)

and with no `.git` at all — a vendored tarball, a published source release —
the version ALONE, with no date and no hash, because a fabricated provenance is
worse than none.

The parts and why each is there, all earned on app_updates:

* **crate version** — "is this current?"
* **HEAD short hash** — "what am I actually running?", the question you have
  when behaviour disagrees with the tree you are reading.
* **an INDEX tree hash** (`git write-tree`) — HEAD cannot see uncommitted work,
  so two different binaries from the same commit are byte-identical without it.
  The 2026-10-01 incident: a freshly installed binary printed the previous
  commit's hash, was judged stale, and was not.
* **`+dirty` when the tree differs from either** — and it must be an explicit
  marker, not an absence: "cannot verify" is not "verified clean", and the one
  sentence the whole clause exists to stop saying is that a build has nothing
  uncommitted.
* **build date** — from `SOURCE_DATE_EPOCH` when set, so a reproducible build
  says the same thing twice.

**WHERE THE ENFORCEMENT LIVES, and why not in the checker.** The checker's own
docstring is the reason: it is STATIC by design, because a gate that must run
every repo's binary is a gate that gets skipped on exactly the repositories it
would catch — wrong architecture, missing toolchain, a crate that is a library
with no binary at all. Asserting the rendered string would require running the
binary, which would move it into the class of gate this one was written to
avoid. So:

* the exact FORMAT is `docs/contracts.md` (this section), and the repo that
  OWNS the format pins its own rendering with its own unit test
  (`crates/cli/src/build_info.rs`, compiled into the build script precisely so
  it can be tested twice);
* the checker enforces what it CAN see — the DECLARATION — and holds an opaque
  `*_PROVENANCE` field to the property behind the format: the build script
  setting it must derive a commit, a date, and **something that sees the working
  tree**. That third one is the half that was silently optional, and dropping it
  makes a dirty build quote a clean commit's hash with no outward sign.

Pin: `tests/test_check_version_provenance.py` (bare version red, truthful
version green, `src/lib.rs` reached, opaque clause without the working-tree
derivation red) and `checks/check_version_provenance.py --probe`.

### 15a. The checker's SCOPE is a claim too, and a dead baseline entry is a finding

The provenance checker's scope was `src/main.rs` and `src/bin/*.rs`. A clap
derive on a struct in `src/lib.rs` with a thin `main.rs` calling `parse()` is
the ordinary way to write this — it is how app_updates does it — so the one file
that DECLARED the version was the one file the checker never read. Worse, the
repo's `.gates-version-baseline.json` listed `crates/cli/src/lib.rs`: an entry
suppressing nothing while reading as debt paid. Both are now fixed, and a
baseline entry naming a file that declares no version is a **finding**, so the
list can only shrink. A baseline is a ratchet; an entry that suppresses nothing
is a claim about the gate, not about the code.

The same reasoning drove two more scope corrections, each found by widening:

* a package is library-only when it declares no binary — no `[[bin]]`, no
  `src/main.rs`, no `src/bin/*.rs` — not merely "no `[[bin]]` and has a
  `src/lib.rs`", which skipped every crate that ships a library and an
  auto-discovered binary together;
* a crate's `build.rs` is looked for in its OWN package, not the first
  matching root. The repository root is itself a package root, the roots are
  sorted, and `path.is_relative_to(root)` is true for every workspace member —
  so the search came up empty and every finding was "no build script sets it":
  true, and about the wrong directory. It stayed invisible because the only
  files reaching that code path sat in a baseline, which skips them BEFORE the
  lookup. A finding suppressed upstream hides the defect downstream.

Pin: `tests/test_check_version_provenance.py`, including two calibration tests
that patch the checker to the broken shape and assert the suite goes red.

## 16. A cross-file reference is a claim about another file, checked against that file

`goh md-links` resolves every relative markdown link in a repo's own docs
to an existing file AND an existing anchor. Both halves fail silently: a link
to a heading that does not exist renders, looks like a link, and 404s on click,
and the anchor is not the heading — GitHub lowercases, drops the punctuation and
turns spaces into hyphens, so `#48-what-90-can-actually-do-measured` must be
DERIVED from a heading and is wrong whenever a human types it.

Three scope decisions, each of which was wrong first:

* **Code is skipped for LINKS and not for ANCHORS.** Fenced blocks, indented
  blocks and inline code spans contain links meant not to resolve (docs about
  markdown syntax), so they are not scanned. But a code span inside a HEADING
  contributes its inner text to the slug: blanking it turned
  `4.8. What `.90` can actually do` into `48-what-------can-actually-do`, and
  the gate rejected the very link it was written to check.
* **A link resolves against the LINKING file**, not the repo root, so
  `../README.md` from inside `docs/` works. A DIRECTORY resolves too
  (`[docs/decisions/](docs/decisions/)` is how ZoneWM points at a folder of
  records); requiring a file reports every such link in the estate as broken.
* **Out-of-scope links are SKIPPED and COUNTED** — http(s), `mailto:`,
  site-absolute. Nothing in the tree can know whether a remote document still
  exists, and a gate that pretends to cries wolf; but the count is reported, so
  a file of nothing but http links never prints the same sentence as a file with
  real links. A link pointing OUTSIDE the repository is a finding: it cannot be
  verified, and unverifiable read as fine is how the next one ships.

Pin: `tests/test_check_md_links.py` (the audit's own mis-derived anchor is red,
the right one is green, a missing file is red, each example shape is quiet),
against the native `goh md-links`.

## 17. A wait is bounded, and a leak is reported by the run that made it

media_server, 2026-10-03: `crates/archive-torznab-rs/tests/test_net_and_bin.rs` spawned the real
binary with `--bind 127.0.0.1:0` — a server that loops forever by design — and reaped it with an
explicit `child.kill(); child.wait();` below four lines that can panic. Nine orphans accumulated,
each holding the cargo build lock, so every later `cargo test` blocked with no output at all, and
the leak itself was invisible because the run that would have reported it was the run that had been
killed. Four separate rules came out of it, and each answers a different question:

* **A reap must survive a panic.** `goh unreaped-spawn`, because "is there a kill in
  this function" is the wrong question — the reap EXISTED in that file, and the ordinary outcome of
  a failing test is to skip it. A guard is the only shape that gets there (`Drop`, `with`, a
  `try`/`finally` that reaps, a shell `trap`), and it is judged by what it DOES.
* **A wait is bounded.** `GOH_STEP_TIMEOUT` for `goh_step`, `GOH_LCI_TIMEOUT` for `local_ci.sh`,
  both ON by default, both PRINTED on every run. Before this, `local_ci.sh`'s ceiling was unset in
  every repo in the estate and `goh_step` had none at all, so the same hang was bounded in one path
  and unbounded in the other.
* **The bound sweeps the SUBTREE.** `lib/bounded_run.py` runs each step in its own process group
  and `killpg`s it. The sweep this replaced was `pkill -P "$pid"` — direct children only — which,
  measured against `bash -c 'sleep 400 & wait'`, left two grandchildren alive. Those are the
  processes that make the NEXT run hang, so the mechanism meant to unstick a hung step was leaving
  the thing that caused it.
* **A leak is reported by the run that made it.** `lib/orphan_canary.py` diffs the process table
  either side of every step. The ceiling cannot see this case: the step exits 0 and the orphan
  outlives it, which is why the leak was silent for a day.
* **The scope is the TREE, because a guard is a CROSS-FILE contract.** `goh unreaped-spawn`'s
  subject is the repository's test scope, at both tiers: `--staged` reads the INDEX (the tree the
  commit produces) and full scope reads the WORKTREE. A scope of "the files this commit brings"
  could not see the contract in either direction, and both halves were found in the estate on
  2026-10-20: a file that USES a shared guard was reported as an unreaped spawn (the guard was
  invisible, so the wrap looked like a bare `Child` — `routines` gave three of its test files a
  guard of their own to get past it, which is the workaround, not the fix), and a commit that
  BROKE a shared guard was green (the files that rely on it were not in scope, so the tree leaked
  and the gate passed it). The cost is a whole-tree read at commit time, measured 2026-10-20:
  363 test files in 0.07 s here, 420 in 0.16 s in `media_server`, the index's blobs fetched in
  one `git cat-file --batch`. What it no longer does: judge only the commit's files — a finding in
  a file the commit does not touch is a finding, because the leak it names is in the tree the
  commit produces.
* **A spinner is OWNED, and the owner is reaped (2026-10-20, the load class).** The house's load
  tool was right about itself and the suite that tested it was not: the tool registers every
  spinner it starts and kills exactly those on the way out, but the TEST killed the TOOL with
  SIGKILL, so the tool's cleanup never ran, and its spinners were re-parented to launchd and left
  burning a core each — eight of them, orphaned by a pytest that died, about an hour of CPU
  apiece. Two rules came out of it:
  * the OWNER of a process is responsible for it, and a test that FAILS owns it too: the failure
    path is the one that never reaches the cleanup. The suite's owner is `tests/load_kit.py`, and
    its last act proves that nothing the suite started outlived it — the check that went RED on
    the suite of that day, and that names the directory (so the test) a leak was left in.
  * a RECORD can VANISH: the registry of an experiment that ran in a temporary directory is
    pruned with that directory, so the registry cannot reach every orphan. The backstop has a
    second way to one — the house's own spinner shape, matched WHOLE, orphaned only, and only
    while the owner the release pipe names is gone. That question needs a name lookup, which is
    the RECORDED EXEMPTION in `kill_by_name_allow.json`: the lookup and the kill sit on ONE line
    so the rule sees them, and the entry's narrowness is pinned by
    `tests/test_load_orphan_exemption.py` — a stale entry, a broadened call, or an entry re-keyed
    to a broadened call is RED.

Pin: `tests/test_check_no_unreaped_spawn.py` (the incident quoted from `media_server@052772a`, red
on the real pre-fix file and green on the same file after its fix, run against the native through
`tests/unreaped_spawn_table*.py`), `tests/test_check_no_unreaped_spawn_scope.py` (the scope: a
shared guard is protection to the file the commit brings, a commit that breaks one is red, and the
staged scope reads the index and not the worktree), `tests/test_bounded_run.py` (the grandchild case, and a passing step passes
through untouched), `tests/test_orphan_canary.py` (the incident's shape is red, a clean step is
silent, another program's process is counted and never failed on),
`tests/test_load_tool.py` (the load class: the owner reaps on the failure path, and the leak
check is shown a real leak so it cannot go quiet), `tests/test_load_reap.py` (the whole chain: a
leak the registry cannot name is cleaned by the minute job, and the negative controls -- a live
parent, a live owner, another shape, a command line that only looks like the shape -- are left
alone), `tests/test_load_orphan_exemption.py` (the recorded exemption: the house's own rule is
green with the entry and red without it, a stale entry is red, and a broadened call is red),
and `checks/check_estate_corpus.py`'s `no_unreaped_spawn` entry (the incident planted inside 611 real
files from media_server).

## 18. A number in prose is re-derived from the tree, and only a MARKED one

Three adversarial reviews of the games estate converged on one finding (2026-10): **the prose is
written one step ahead of the mechanism, in files where the prose is far more convincing than the
mechanism is load-bearing.** `roadmap_state.py` said "64 declared gates" against 70 declared in
`verify.py` — a regex that cannot match a label carrying a second space, fixed twenty lines from the
docstring that still quoted its old output. `check_mcp_server.py` said "477 lines and 18
characterization tests" for 321 and 20, in the gate whose entire job is counting. `.gatesrc`
described three "grandfathered" exemptions for a file that declares none, nine lines after saying so.
`gaf/README.md` said 86 files / 64 test files / 1059 tests for 101 / 91 / 1732. All six were found by
hand, at hours each, and none was noticed by a gate, a test or a review of the diff.

* **The claim form is EXPLICIT, and the looser rule was rejected.** A claim is read only when the
  number AND the thing it counts are both in backticks — the estate's existing convention that a
  backtick means "this is the evidence" — or when the line carries a leading `claim:`. A bare number
  beside a path is prose. The looser rule ("any number in a sentence naming a path") is English
  interpretation, and it is wrong in a direction nobody notices: it cannot tell an inventory from an
  incident, a current claim from a quoted one, or a count from a version — and when it is wrong it is
  wrong loudly and in bulk, which is how a gate gets switched off. The cost of this limit is stated
  plainly: the six defects above are BARE numbers, so this gate would have caught none of them as
  they stood. What it buys is that the first edit after a defect re-derives the number instead of
  retyping it.
* **A claim the tree cannot answer is a FINDING, named for why** — never a skip. A number nobody is
  re-deriving is the state the whole gate exists to end, and reporting nothing about it would be the
  worst available answer.
* **The printed command is part of the finding, and `--probe` EXECUTES it.** "The tree says 64" is a
  claim; "the tree says 64 — `python3 -c …`" is something the reader can check. A finding whose
  command disagrees with the gate is worse than one with no command, because it looks checked.
* **An exemption is a ratchet.** `claim_derivation_allow.json` names the file, the claim and a reason;
  an entry matching nothing is STALE and fails, so a fixed claim takes its exemption with it; and the
  key is the claim itself (its number, unit and target), whitespace collapsed, never the line it sits
  on: an entry holding the claim, or a whole line containing it, survives a re-wrapped paragraph, and
  a changed number is a different claim. The allowlist is never read as prose (its entries quote claims).
* **Opt-in per repo** (`GOH_CLAIM_DERIVATION=1`), because this lands red in every repo in the estate:
  every repo in the estate has this defect, and a gate that goes red in twenty places on the day it
  lands is a gate that gets disabled. Each repo turns it on when it has marked its claims or fixed
  them.

Pin: `tests/test_check_claim_derivation.py` (every unit red-before and green-after, the negative
controls that must stay quiet, every printed command executed and compared, the staged-scope
boundary, and the allowlist ratchet in both directions) against the native `goh claim-derivation`,
and
`checks/check_estate_corpus.py`'s `claim_derivation` entry (a false claim planted inside a real
markdown file from a real estate repo).

## 19. A running gate is pinned to the bytes it started with

Every consumer executes these scripts live from this checkout, and bash reads a script LAZILY, by
byte offset. An edit, pull or checkout here while a consumer's gate runs shifts every unread offset:
routines' pre-push died with `coverage_gate.sh: line 485: syntax error near unexpected token ';;'`
(2026-10-05) on a line that is not a syntax error. Every EXECUTED bash script wraps its body in
`{ # parse-guard … exit; } # parse-guard`, so bash parses the whole group before running any of it.
Sourced files need nothing (`source` reads the whole file first); `release.sh` re-execs a temp copy
of itself instead, which it can afford because it never resolves siblings from its own path.

Pin: `tests/test_parse_guard.py` (every executed script carries the guard; a guarded script
rewritten mid-run completes as written, and the unguarded control does not).

## 20. A gate's tool and a gate's lockfile are inputs it refuses to run without

* **A missing tool FAILS, before any slow step** (`goh_require` in `gates/_common.sh`): cargo-machete,
  swiftlint and shellcheck each used to warn and exit 0, a step that never ran under a green result.
* **Advisories are judged BITING, and never from a cache** (BACKLOG 6.12). `rust_gate.sh` runs
  `cargo audit --deny warnings --deny unmaintained --deny unsound --deny yanked` from the workspace
  root before the crate group, outside every proven record: the advisory database is an input no key
  names, so yesterday's green lockfile can be today's red one. Without the deny list cargo-audit
  exits 0 on an unmaintained crate (calibrated 2026-10-12). `check_audit_ignores.py` then refuses an
  ignore entry with no reason and one whose advisory no longer fires.
* **`Cargo.lock` is an input, never an output.** `rust_gate.sh` refuses a missing lockfile, runs
  `cargo metadata --locked` first, passes `--locked` to every resolving cargo call (here, in
  `rust_manifest_gate.sh` and in `coverage_gate.sh`), and ends by checking the lockfile is
  byte-identical to the one it started from — so a step that takes no such flag still cannot launder
  a re-resolve (antiknob carried `tools/lock_guard.sh` for this, 2026-10-04).

Pin: `tests/test_required_tools.py` (one tool removed from PATH at a time),
`tests/test_rust_gate.py` (`test_a_stale_lockfile_fails_the_gate_and_is_not_rewritten`,
`test_a_missing_lockfile_is_refused_not_generated`, and a source read that finds any resolving cargo
call without `--locked`).

## 21. A pre-push run owns one directory, and nothing it leaves outlives it

`push_gate.sh` exports to `<export root>/<repo>-<hash>/<repo>` beside an `.owner` stamp (pid + start
time). The path is STABLE per repo, so cargo's `{workspace-path-hash}` build-dir repeats and
dependencies stay warm; a concurrent push of the same repo takes a private path with its build-dir
inside the run. Cleanup removes the run directory and NAMES anything the gate wrote outside its
tree; each run first reaps run directories whose owner is dead (a SIGKILL leaves the directory, which
`git worktree prune` never removes). Measured before: one three-day-old worktree, 230 `*.out` files,
and 24 orphaned cargo build-dirs (30 GB).

Pin: `tests/test_push_gate_runs.py`.

## 22. A shared gate lands with its canary case, in the same commit

Every installed `checks/check_*` is driven by a case in `checks/_canary_cases.py` -- red over a
tree that violates it, green over a clean one -- or named in its `UNCOVERED` with the reason a map
of files cannot drive it. The table lived in game_asset_factory until 2026-10-19 and demanded the
same thing from there, so each new gate turned GAF's next unrelated push red until someone wrote
its case (at least eight times from 2026-10-01). Beside the gates, the case is part of the change
that adds the gate, and an entry naming a gate that is gone is stale.

Pin: `tests/test_canary.py` (every case run both ways; a new uncased gate, a blind fixture and a
noisy gate each red).

## 23. The estate is a registry, and a path that is gone is named

`install.sh` records every repo it installs into -- one line per toplevel, in goh's own state
directory beside the estate audit's state file (`installed_repos`). Every estate sweep reads that
registry UNIONED with the directory it walks (`GOH_ESTATE_ROOT`), never one or the other: a
consumer `install.sh` wired goh into outside the root is in the estate, and the estate is where
the audit sweeps look.

A registered path that is GONE is named -- in the sweep's output, in the state file it records,
and in the session hook that reads that file back -- never dropped in silence. The registry is
keyed by PATH, so a repository that moved on disk is a gone path AND a new one, and the owner is
told both rather than left to guess which repository stopped being audited.

Pin: `tests/test_estate_registry.py` (an install outside the root is audited; a gone path is
named; a moved repo is a gone path and a new one; the seed is additive; the machine's registry is
never a test's).

## 24. A report is ONE write, so the intermediate state does not exist

A writer that prints a header and then its details in two writes leaves a state that is not
final, and a reader can land in it: the gate-slot wait report was two writes, the window between
them was 56 us, and a reader polling every millisecond read a half-written report as final
(1 of 20 trials against the real writer, 20 of 20 against the instrumented one).

No rule on the READER's side can prevent that, because the soundness of a wait depends on the
writer's write ORDER, which a reader cannot know -- the static rule was written, measured and
DELETED in E39 (it flagged two SAFE sites in this suite). So the writer's report goes through
`tui/lib.py`'s and `tui/lib.sh`'s `report`, which writes the header and its details in ONE
write(2)/printf: the state that carries the header already carries the details, so there is no
intermediate state to read. The two halves of the TUI are pinned to render the same bytes, so
the guarantee does not depend on which half a writer uses.

Pin: `tests/test_tui_report.py` (the two halves agree byte for byte; each writes the report
once; the report renders exactly what the two writes it replaces rendered),
`tests/test_gate_slot_race.py` (the reader never sees a half-written report, the writer's own
stream says the report was one write, and the probe is shown to still catch the old two-write
shape -- an instrument that cannot fail proves nothing), and `tests/test_report_writes.py` (the
WRITER-side ratchet: an info write immediately followed by a step write is a report in two
pieces, by the house's own definition of `step`, and there are none in this tree).

## 25. A template is judged by its SUBSTITUTED form, and a scope is stated, not implied

A file that is not JavaScript on its own -- a template, carrying placeholders whose values are
filled in later -- has exactly two honest readings: judge the file as it stands (and refuse every
template), or judge the file with its placeholders filled in. EXCLUDING it is the third reading
and it is the one that hides the subject: an excluded template is never parsed, so a broken
skeleton -- an unbalanced brace, a stray backtick -- is a defect nobody sees, and the gate exists
because that defect is invisible everywhere else.

So the placeholders are substituted with a NEUTRAL EXPRESSION and the RESULT is parsed. The
substitution is narrow on purpose: only the placeholders the tree's own convention defines
(`${NAME}`, `@NAME@`), because a name is always a valid expression and so nothing can hide behind
it. A hole holding an EXPRESSION (`${window.TIMEOUT}`) is left alone -- it is JavaScript, and the
parse still judges it -- and a malformed hole is left alone too, so the parse fails on it.

Two more rules ride with it, and both are about the SCOPE rather than the subject:

  * THE SCOPE IS STATED. Every run prints how many files it measured, what shape their paths had,
    and every path the vendor and build list took out of the scope. A gate that measured nothing
    and a gate that measured everything and found nothing wrong print the same word otherwise, and
    an exclusion can hide the subject.
  * A SOURCE THE ENGINE CANNOT READ IS REFUSED, NEVER PASSED. The fallback engine parses a classic
    script only, because parsing a module there means RUNNING it, and a gate does not run the
    repository's code. Such a source is named as unmeasured (exit 2), and the engine that can
    read it without running it -- node, with `node --check` on a `.mjs` file -- reads the module
    goal.

Pin: `tests/test_check_js_syntax.py` (a template that is not JavaScript on its own passes; a
template whose substituted form does not parse is red; a malformed hole is still judged; the scope
is printed; a module source under the fallback is refused rather than passed), and
`checks/_canary_cases.py` (the two canary cases: a stray backtick, and a broken template).

## 26. The house has ONE way to make load, and a spinner is refused at every level

A backgrounded busy loop has NOTHING to reap it. When the shell that owns it is killed, the
spinner is re-parented to launchd (ppid 1) and keeps burning a core for hours: twenty-four of
them, four cores each, seventy-eight minutes of CPU apiece, four and a half hours old, on
2026-10-21 -- hand-written, in a file, and invisible to every level the house had.

So the class is closed at three levels, and the rule behind them is ONE definition
(`lib/busyloop.py`): a busy loop is a `while`/`until` whose body is nothing but `:`/`true`/`false`;
TEXT is not CODE (a loop inside a string, a heredoc body or a comment is text, and
`echo 'while :; do :; done'` writes a loop, it does not run one); a loop RUNS when the line
carrying it also carries a background operator.

  * THE TOOL BOUNDARY. The Claude Code guard (matcher `Bash|Write|Edit|MultiEdit`) refuses a
    COMMAND and a FILE that run one, and names `tools/load.py` -- the one way to make load, which
    registers its spinners, kills exactly those, and reaps itself -- with `# load-ok: <reason>`
    as the one escape.
  * THE COMMIT. A tracked shell file that runs one is refused by `goh busy-loop`, in every repo,
    at both scopes, with no path exemption (a busy loop is not a style) and a floor
    (`GOH_BUSY_LOOP_MIN_FILES`) so a scope that stopped being read is red rather than clean.
  * THE MACHINE. The minute job kills an ORPHAN that has BURNED a core on a busy loop --
    whoever wrote it -- and asks four questions before it touches anything: the parent is 1, the
    CPU time is over the floor, the CPU time is STILL growing, and the code a shell runs contains
    the loop. That last question needs a name lookup, so it is the SECOND RECORDED EXEMPTION in
    `kill_by_name_allow.json`, with the lookup and the kill on ONE line, and a stale or broadened
    exemption is RED.

And the bound that makes the suite safe to kill: every fixture that spins is bounded by its own
clock, so a run that is SIGKILLed leaves no process of its own behind it.

Pin: `tests/test_claude_load_guard.py` (the command and the file), `tests/test_check_busy_loop.py`
(the check, both ways), `tests/test_load_reap.py` (the four questions, the negative controls, and
the killed-run bound), `tests/test_load_orphan_exemption.py` (both exemptions, stale and
broadened).

## 27. A JavaScript source is judged with the BROWSER'S goal, and the goal is named before the engine

A parser is asked "is this source valid?" only once it has been told what KIND of source it is,
and the goals disagree. A top-level `return` is a syntax error in a browser's classic script and
an ordinary statement in CommonJS; a top-level `await` is a syntax error in a classic script and
legal at the top level of a module. So a gate that does not name its goal is a gate whose answer
depends on which engine it happened to ask -- and the engines disagree.

The house's JavaScript gate asked node the way node is usually asked: `node --check`, which
parses a `.js` file with the goal node reads off the nearest `package.json`. That goal is
CommonJS or MODULE, and it ACCEPTS a top-level `return`. The browser REFUSES it. So the gate was
GREEN on a file no page can load, and the page that loads it renders empty: the failure mode of
the gate was INVERTED for exactly the case the estate cares about. (Found by a consumer,
2026-10-21: `divoom-control` built its own checker against the browser's goal for the same
reason, and its own reading of the goal is the one the house now takes.)

So the goal is named FIRST, and it is the browser's:

  * A `.js` file is a CLASSIC SCRIPT -- what a browser's `<script src>` is. A top-level `return`,
    a top-level `await`, `import` and `export` are all refused in it.
  * A `.mjs` file is a MODULE -- what a browser's `<script type="module">` is, and what node's
    ESM is. `import`, `export` and a top-level `await` are legal in it; a top-level `return` is
    not.
  * A `.cjs` file is COMMONJS -- node's, and no browser's. A top-level `return` is legal in it.

THE GOAL COMES FROM THE NAME, NOT FROM THE TEXT. A browser's goal is decided by the HTML that
loads the file, and the HTML is not in the repository the gate reads, so a file cannot know how a
page loads it. The NAME is the one thing in a file that every loader agrees with. Sniffing the
text instead would be a guess with a bad failure direction: a file whose REGEX holds the word
`import` would be called a module, and would then be judged with the goal that ALLOWS a top-level
`await` -- the very defect this contract closes. The name is a fact; the text is a guess. The cost
is stated rather than hidden: a repository that keeps a module in a `.js` file is told to rename
it to `.mjs`.

THE ENGINE ORDER IS THE HOUSE'S, AND IT IS PROVENANCE, NOT PREFERENCE. node is the PRIMARY
engine, because it reaches the browser's goals EXACTLY and runs nothing: `vm.Script` IS the
classic-script goal, `vm.compileFunction` is the CommonJS goal, and `node --check` on a `.mjs`
file is the module goal. macOS's `JavaScriptCore` is the FALLBACK, because it is in the operating
system and a machine with nothing installed still has a parser -- and it reaches the classic goal
in TWO probes, because `new Function` builds a function BODY, which may `return` at its top level
where a classic script may not. A source the fallback cannot judge is REFUSED (exit 2), never
passed: when its first probe says the source is fine and its second (a class static block, where a
`return` is illegal) says it is not, the difference is either a top-level `return` or a
strict-mode-only construct, and the engine's own words tell the two apart.

Pin: `tests/test_check_js_syntax.py` (a top-level `return` and a top-level `await` are red under
node AND under the `JavaScriptCore` fallback that reads the same files with no node installed; a
`.js` file carrying module syntax is red, because a browser loads a `.js` file with `<script
src>`; a `.mjs` file is green and is named in the report as a module; a `.cjs` file with a
top-level `return` is green, because that is node's CommonJS; the clean tree is green).
