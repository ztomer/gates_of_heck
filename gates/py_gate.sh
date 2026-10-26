#!/usr/bin/env bash
# py_gate.sh — the Python gates every house Python project must pass.
#
#   1. ruff check          (the project's own rule set, from pyproject.toml)
#   2. ruff format --check
#   3. with GOH_PY_NOQA_PARITY: no `# noqa` that a second linter the repo runs still reports
#   4. pytest with a coverage floor
#
#   py_gate.sh                      # run from the repo root
#   py_gate.sh <repo>               # run from anywhere
#   py_gate.sh <repo> <pkg_dir>     # sources live in a subdir
#
# Config, from the target repo's .gatesrc:
#   GOH_PY_COV_MIN=95      # coverage floor; unset skips the coverage gate
#   GOH_PY_RUNNER="uv run" # prefix for the toolchain (uv, poetry, hatch...)
#
# Steps run as plain argv inside <pkg_dir> (goh_step_in): no shell-string
# interpolation, so paths and runners containing quotes stay data. KNOWN
# LIMITATION, stated honestly: GOH_PY_RUNNER is whitespace-split into argv
# (`uv run` works; a runner whose own PATH contains a space cannot be
# expressed). Use .venv/bin/python (auto-detected) for such setups.
#
# The coverage step scopes --cov to <pkg_dir> explicitly. Bare `--cov` is a
# vacuous floor: it only measures what pytest imported, so a never-imported
# 0%-covered module is invisible to even GOH_PY_COV_MIN=100. The scope alone
# is not enough either: coverage discovers unexecuted files only inside
# PACKAGES, so a script in a plain directory stayed invisible until a test first
# imported it, and that test then LOWERED the total (ZeroThunder, 2026-10-18:
# 67.03% against 68 with no test failing). The floor is therefore decided by
# gates/py_cov_floor.py over every file the repo holds, the repo's omit honoured.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

repo="${1:-$PWD}"
cd "$repo"
pkg_dir="${2:-$PWD}"
# `pwd -P`, NOT `pwd`, and `/bin/pwd -P`, NOT the bash BUILTIN. coverage matches
# `--cov=<scope>` as a STRING PREFIX against the paths it records, so a logical
# spelling that differs in CASE from the on-disk path scopes every measured file out
# of the report — silently, because the files are still executed. Measured 2026-10-08
# from scripts/: a shell whose PWD said `~/projects/scripts` for the repo at
# `~/Projects/scripts` produced `--cov=/Users/ztomer/projects/scripts`, and
# `[run] patch = subprocess` dropped every CHILD's data with it — 8 shell suites
# reported "ran but its children measured none of" their modules and the floor came
# back 0.00% with 251 tests passing.
#
# The builtin is not enough, and that is measured too: in a shell that has just
# `cd`-ed to a path spelled in the wrong case, bash's `pwd -P` returns THAT spelling
# (it reuses the path it cached for the cd), while `/bin/pwd -P` calls getcwd(3) and
# returns the one the filesystem holds. Same directory, same command, two answers.
pkg_dir="$(cd "$pkg_dir" && /bin/pwd -P)" || die "pkg_dir not found: $pkg_dir"

[ -f .gatesrc ] && . ./.gatesrc
. "$HERE/_py.sh"
goh_py_runner

goh_init "python"
goh_tree_stamp

goh_py_require ruff
goh_py_require pytest
# shellcheck source=gates/_pytest_root.sh
. "$HERE/_pytest_root.sh"
goh_pytest_root  # a concurrent suite cannot prune this one's temp dirs

# PLW1510 is the house floor on top of the project's own rule set (G17b, 2026-10-26): a
# `subprocess.run` must say what a failing child does (`check=True` stops the caller,
# `check=False` says the exit code is judged below). Rolled out to every consumer first.
goh_step_in "$pkg_dir" "ruff check"        "${RUN_ARR[@]}" ruff check --extend-select PLW1510 .
goh_step_in "$pkg_dir" "ruff format check" "${RUN_ARR[@]}" ruff format --check .
if [ -n "${GOH_PY_NOQA_PARITY:-}" ]; then
    # A `# noqa` ruff honours and the second linter the repo runs ignores (gates/py_noqa_parity.py, 4.5).
    goh_step_in "$pkg_dir" "noqa parity with $GOH_PY_NOQA_PARITY" \
        "$GOH_PY" "$HERE/py_noqa_parity.py" --linters "$GOH_PY_NOQA_PARITY" --runner "$RUN" --all
fi

if [ -n "${GOH_PY_COV_MIN:-}" ]; then
    # pytest measures; the floor is py_cov_floor's, under the repo's own interpreter (its coverage
    # reads the repo's config). A runner ending in `-m` takes a module; `uv run` and kin take
    # `python -m`. PYTHONPATH carries goh for the helper and for tui.lib.
    if [ "${RUN_ARR[${#RUN_ARR[@]}-1]}" = "-m" ]; then
        PYMOD=("${RUN_ARR[@]}")
    else
        PYMOD=("${RUN_ARR[@]}" python -m)
    fi
    goh_step_in "$pkg_dir" "pytest (measuring coverage)" \
        "${RUN_ARR[@]}" pytest -q --cov="$pkg_dir" --cov-report=
    goh_step_in "$pkg_dir" "coverage >= ${GOH_PY_COV_MIN}% over every repo file" \
        env PYTHONPATH="$HERE/..:$HERE${PYTHONPATH:+:$PYTHONPATH}" \
        "${PYMOD[@]}" py_cov_floor "${GOH_PY_COV_MIN}"
else
    goh_step_in "$pkg_dir" "pytest" "${RUN_ARR[@]}" pytest -q
    warn "no coverage floor — set GOH_PY_COV_MIN in .gatesrc"
fi
rm -rf "$PYTEST_DEBUG_TEMPROOT"

goh_done
exit
} # parse-guard
