#!/usr/bin/env bash
# The gates_of_heck test suite: parallel under pytest-xdist when installed.
# --dist loadgroup: unmarked tests spread by load; files sharing an
# xdist_group marker stay on ONE worker. That grouping was made for the release
# tests, when test_release_hardening.py corrupted the real release.sh MID-RUN
# and test_release_kit.py, on another worker, read the corrupted bytes; it now
# rewrites a copy (no test moves the checkout: tests/_tree_guard.py).
# -n 12: measured 2026-10-06 against 8 (67/79 s vs 74/96 s, interleaved) once
# the spawn cuts landed; 16 was a draw with 12 under load, and leaves no
# headroom for the other sessions sharing the box.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=/Users/ztomer/Projects/gates_of_heck/tui/lib.sh
. "$GOH/tui/lib.sh"
cd "$GOH"
if python3 -c "import xdist" 2>/dev/null; then
    exec python3 -m pytest tests/ -q -n 12 --dist loadgroup
fi
warn "pytest-xdist not installed — serial suite (pip3 install pytest-xdist)"
exec python3 -m pytest tests/ -q
exit
} # parse-guard
