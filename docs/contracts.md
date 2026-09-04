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
