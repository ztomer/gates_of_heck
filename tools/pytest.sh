#!/usr/bin/env bash
# The gates_of_heck test suite: parallel under pytest-xdist when installed.
# --dist loadgroup: unmarked tests spread by load; files sharing an
# xdist_group marker stay on ONE worker -- for SHARED state only. The release
# tests' group went 2026-10-06: test_release_hardening.py rewrites a copy of
# release.sh now, not the real one, and tests/_tree_guard.py fails any run in
# which a test moves the checkout.
# -n 12: measured 2026-10-06 against 8 (67/79 s vs 74/96 s, interleaved) once
# the spawn cuts landed; 16 was a draw with 12 under load, and leaves no
# headroom for the other sessions sharing the box.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/../lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=/Users/ztomer/Projects/gates_of_heck/tui/lib.sh
. "$GOH/tui/lib.sh"
cd "$GOH"
# shellcheck source=gates/_pytest_root.sh
. "$GOH/gates/_pytest_root.sh"
goh_pytest_root  # concurrent suites cannot prune each other's temp dirs; kept after a failure
# ONE SUITE AT A TIME, never a suite inside itself (G2d, 2026-10-25). A test that ran this runner
# with a stub `python3` first on PATH stopped intercepting the day the runner moved to the house
# interpreter: the runner ran the REAL suite inside the test, whose copy of that test ran it again --
# 33 nested suites and ~590 processes, load ~229, until killed by hand. The marker is not GOH_-
# prefixed, so a hermetic test env that drops GOH_* keeps it; a test that means a nested run (with a
# stub interpreter through GOH_PY) says so with GATES_OF_HECK_SUITE_NESTED_OK=1.
if [ -n "${GATES_OF_HECK_SUITE_RUNNER:-}" ] && [ -z "${GATES_OF_HECK_SUITE_NESTED_OK:-}" ]; then
    die "tools/pytest.sh started inside a running goh suite (runner pid $GATES_OF_HECK_SUITE_RUNNER) -- a nested suite recurses; a test that means it sets GATES_OF_HECK_SUITE_NESTED_OK=1 and a stub GOH_PY" 2
fi
# ONE SUITE PER CHECKOUT (G2e, 2026-10-25). Two suites of one tree at once share its bin/, its
# target/ and its proven cache, and each runs at half speed beside the other -- the full gate ran
# exactly that (a test step beside the coverage group's run) until G2e. A second top-level run is
# REFUSED, naming the holder, rather than queued: a wait looks like a hang. The lock is a symlink
# whose target is the holder's pid, made atomically by `ln -s`; a holder that is gone is reaped. A
# run nested on purpose (GATES_OF_HECK_SUITE_NESTED_OK) is inside the holder's run and takes none.
# GATES_OF_HECK_SUITE_LOCK names another lock (a test of this one).
if [ -z "${GATES_OF_HECK_SUITE_RUNNER:-}" ] && [ -z "${GATES_OF_HECK_SUITE_NESTED_OK:-}" ]; then
    # The checkout's OWN git dir (a worktree has its own), never a calling hook's GIT_DIR.
    SUITE_LOCK="${GATES_OF_HECK_SUITE_LOCK:-$( (unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR
        git -C "$GOH" rev-parse --absolute-git-dir 2>/dev/null) || printf %s "$GOH")/goh-suite.lock}"
    until ln -s "$$" "$SUITE_LOCK" 2>/dev/null; do
        holder="$(readlink "$SUITE_LOCK" 2>/dev/null || true)"
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            die "another goh suite (pid $holder) is running in this checkout -- one suite at a time ($SUITE_LOCK)" 2
        fi
        rm -f "$SUITE_LOCK"   # its holder is gone: a killed run's lock
    done
    trap '[ "$(readlink "$SUITE_LOCK" 2>/dev/null)" = "$$" ] && rm -f "$SUITE_LOCK"' EXIT
fi
export GATES_OF_HECK_SUITE_RUNNER=$$
ARGS=(-q)
if "$GOH_PY" -c "import xdist" 2>/dev/null; then
    ARGS+=(-n 12 --dist loadgroup)
else
    warn "pytest-xdist not installed — serial suite (pip3 install pytest-xdist)"
fi
rc=0
"$GOH_PY" -m pytest tests/ "${ARGS[@]}" || rc=$?
if [ "$rc" -eq 0 ]; then
    rm -rf "$PYTEST_DEBUG_TEMPROOT"
else
    info "the failed run's temp root is kept: $PYTEST_DEBUG_TEMPROOT"
fi
(exit "$rc")
exit
} # parse-guard
