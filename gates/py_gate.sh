#!/usr/bin/env bash
# py_gate.sh — the Python gates every house Python project must pass.
#
#   1. ruff check          (the project's own rule set, from pyproject.toml)
#   2. ruff format --check
#   3. pytest with a coverage floor
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
# 0%-covered module is invisible to even GOH_PY_COV_MIN=100.
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

command -v python3 >/dev/null 2>&1 || die "python3 not on PATH"
goh_py_require ruff
goh_py_require pytest

goh_step_in "$pkg_dir" "ruff check"        "${RUN_ARR[@]}" ruff check .
goh_step_in "$pkg_dir" "ruff format check" "${RUN_ARR[@]}" ruff format --check .

if [ -n "${GOH_PY_COV_MIN:-}" ]; then
    goh_step_in "$pkg_dir" "pytest (coverage >= ${GOH_PY_COV_MIN}%)" \
        "${RUN_ARR[@]}" pytest -q --cov="$pkg_dir" --cov-report=term-missing \
        --cov-fail-under="${GOH_PY_COV_MIN}"
else
    goh_step_in "$pkg_dir" "pytest" "${RUN_ARR[@]}" pytest -q
    warn "no coverage floor — set GOH_PY_COV_MIN in .gatesrc"
fi

goh_done
exit
} # parse-guard
