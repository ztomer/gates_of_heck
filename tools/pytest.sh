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
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=/Users/ztomer/Projects/gates_of_heck/tui/lib.sh
. "$GOH/tui/lib.sh"
cd "$GOH"
# shellcheck source=gates/_pytest_root.sh
. "$GOH/gates/_pytest_root.sh"
goh_pytest_root  # concurrent suites cannot prune each other's temp dirs; kept after a failure
ARGS=(-q)
if python3 -c "import xdist" 2>/dev/null; then
    ARGS+=(-n 12 --dist loadgroup)
else
    warn "pytest-xdist not installed — serial suite (pip3 install pytest-xdist)"
fi
rc=0
python3 -m pytest tests/ "${ARGS[@]}" || rc=$?
if [ "$rc" -eq 0 ]; then
    rm -rf "$PYTEST_DEBUG_TEMPROOT"
else
    info "the failed run's temp root is kept: $PYTEST_DEBUG_TEMPROOT"
fi
(exit "$rc")
exit
} # parse-guard
