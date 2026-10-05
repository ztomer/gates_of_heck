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
`tools/gate.sh` / `.gatesrc` only when absent. `pre-push` names a
missing `tools/gate.sh` instead of exec-failing.
Pin: `tests/test_install.py`, `tests/test_wiring.py`.

## 9. Config schema cannot drift (`docs/config.md`, `.gatesrc.example`)

Every `GOH_*` key read in `gates/ checks/ lib/ tools/ hooks/` appears
in `docs/config.md`; `.gatesrc.example` invents none. Adding a key
without documenting it fails the suite.
Pin: `tests/test_config_schema.py`.

## 10. Parallel suite shares nothing mutable (`tools/gate.sh --full`)
`--full` runs `tools/pytest.sh` (`pytest -n 8 --dist loadgroup`). Files sharing an
`xdist_group` marker stay on ONE worker: `test_release_hardening.py`
corrupts `tools/release-kit/release.sh` MID-RUN on purpose, and
`test_release_kit.py` runs real releases — split across workers, the
latter reads corrupted bytes and dies with a syntax error (found
2026-09-04 the first time the suite ever ran parallel). `test_desktop_lock`
has its own group: its tests take the REAL machine-wide mutex, so
splitting them across workers means contending with themselves.
Pin: the full suite green under `-n 8 --dist loadgroup`; serial green
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
Python (`check_empty_scope`, `check_probes_pass`), `unset $(git
rev-parse --local-env-vars)` in shell (`push_gate.sh`, `_proven.sh`),
`goh_testkit::git_command`/`git_in`/`goh_at` in Rust, and
`tests/conftest.py` at import. Never used for the repo being gated:
there `GIT_INDEX_FILE` names the index being committed.
Pin: `tests/test_hook_git_env.py`,
`crates/goh-testkit/tests/hook_git_env.rs`.

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

## 14. A committed artifact that makes a claim is checked against its source

`Cargo.lock` states each workspace crate's version; `Cargo.toml` asks for one.
They are the same claim written twice, and `check_lock_version.py` compares
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
bumped in one place only is red) and `checks/check_lock_version.py --probe`,
which `check_probes_pass` runs.

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

`check_md_links.py` resolves every relative markdown link in a repo's own docs
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
the right one is green, a missing file is red, each example shape is quiet) and
`checks/check_md_links.py --probe`.

## 17. A wait is bounded, and a leak is reported by the run that made it

media_server, 2026-10-03: `crates/archive-torznab-rs/tests/test_net_and_bin.rs` spawned the real
binary with `--bind 127.0.0.1:0` — a server that loops forever by design — and reaped it with an
explicit `child.kill(); child.wait();` below four lines that can panic. Nine orphans accumulated,
each holding the cargo build lock, so every later `cargo test` blocked with no output at all, and
the leak itself was invisible because the run that would have reported it was the run that had been
killed. Four separate rules came out of it, and each answers a different question:

* **A reap must survive a panic.** `checks/check_no_unreaped_spawn.py`, because "is there a kill in
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

Pin: `tests/test_check_no_unreaped_spawn.py` (the incident quoted from `media_server@052772a`, red
on the real pre-fix file and green on the same file after its fix; narrowing the ordering rule alone
turns `--probe` red), `tests/test_bounded_run.py` (the grandchild case, and a passing step passes
through untouched), `tests/test_orphan_canary.py` (the incident's shape is red, a clean step is
silent, another program's process is counted and never failed on), and
`checks/check_estate_corpus.py`'s `no_unreaped_spawn` entry (the incident planted inside 611 real
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
  match collapses whitespace, because an exemption a routine `ruff format` can revoke is not one.
* **Opt-in per repo** (`GOH_CLAIM_DERIVATION=1`), because this lands red in every repo in the estate:
  every repo in the estate has this defect, and a gate that goes red in twenty places on the day it
  lands is a gate that gets disabled. Each repo turns it on when it has marked its claims or fixed
  them.

Pin: `tests/test_check_claim_derivation.py` (every unit red-before and green-after, the negative
controls that must stay quiet, every printed command executed and compared, the staged-scope
boundary, and the allowlist ratchet in both directions), `checks/check_claim_derivation.py --probe`
(the same table through the checker's own entry point, plus its exit codes), and
`checks/check_estate_corpus.py`'s `claim_derivation` entry (a false claim planted inside a real
markdown file from a real estate repo).
