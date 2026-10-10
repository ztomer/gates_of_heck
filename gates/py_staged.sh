#!/usr/bin/env bash
# py_staged.sh — the CHEAP half of py_gate.sh, at COMMIT time, on the staged .py files only.
#
#   1. ruff check          on staged *.py
#   2. ruff format --check on staged *.py
#
#   py_staged.sh                # from the repo root (a repo's tools/gate.sh calls it)
#   py_staged.sh <repo>
#
# Rule 14 (a local gate weaker than the next one up lets you commit red and find out at push):
# two repos each spent a round of fix-up commits on findings the PUSH gate reported one at a
# time -- ruff UP031, a format drift, RUF007 -- that this would have named at the commit.
# ZeroThunder grew the block by hand in its own gate.sh; a second repo needing the same block is
# the house rule that gates live HERE once. No pytest, no coverage: those stay at push, where the
# whole tree is measured. Exits 0 when nothing Python is staged.
#
# The runner resolution is gates/_py.sh's, shared with py_gate.sh (GOH_PY_RUNNER, else .venv, else python3).
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"
repo="${1:-$PWD}"
cd "$repo"
[ -f .gatesrc ] && . ./.gatesrc
. "$HERE/_py.sh"
goh_py_runner

goh_init "python (staged)"
STAGED=()
while IFS= read -r f; do
    [ -n "$f" ] && STAGED+=("$f")
done < <( (goh_bind_hook_index  # the index being committed (gates/_git_env.sh); ruff gets none
          git diff --cached --name-only --diff-filter=ACM -- '*.py') 2>/dev/null || true)
# macOS ships bash 3.2: no mapfile, and "${arr[@]}" on an empty array trips `set -u` there.
if [ "${#STAGED[@]}" -eq 0 ]; then
    info "no staged .py files"
    goh_done
    exit 0
fi
goh_py_require ruff
goh_step "ruff check (staged)"        "${RUN_ARR[@]}" ruff check "${STAGED[@]}"
goh_step "ruff format check (staged)" "${RUN_ARR[@]}" ruff format --check "${STAGED[@]}"
goh_done
exit
} # parse-guard
