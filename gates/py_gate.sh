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
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

repo="${1:-$PWD}"
cd "$repo"
pkg_dir="${2:-$PWD}"
pkg_dir="$(cd "$pkg_dir" && pwd)" || die "pkg_dir not found: $pkg_dir"

[ -f .gatesrc ] && . ./.gatesrc
RUN="${GOH_PY_RUNNER:-}"

# Prefer a project venv over whatever is on PATH — a gate that silently used a
# different interpreter than the project is a gate measuring the wrong thing.
if [ -z "$RUN" ] && [ -x .venv/bin/python ]; then
    RUN=".venv/bin/python -m"
elif [ -z "$RUN" ]; then
    RUN="python3 -m"
fi
read -r -a RUN_ARR <<<"$RUN"

goh_init "python"

command -v python3 >/dev/null 2>&1 || die "python3 not on PATH"

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
