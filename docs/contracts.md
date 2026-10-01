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
Pin: `tests/test_golden_core.py`.

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
