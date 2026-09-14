#!/usr/bin/env bash
# The gates_of_heck test suite: parallel under pytest-xdist when installed.
# --dist loadgroup: unmarked tests spread by load; files sharing an
# xdist_group marker stay on ONE worker. That grouping exists for the release
# tests: test_release_hardening.py corrupts tools/release-kit/release.sh
# MID-RUN on purpose, and test_release_kit.py runs real releases -- split
# across workers, the latter reads corrupted bytes and dies. loadfile alone
# only groups within a file, not across the two.
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=/Users/ztomer/Projects/gates_of_heck/tui/lib.sh
. "$GOH/tui/lib.sh"
cd "$GOH"
if python3 -c "import xdist" 2>/dev/null; then
    exec python3 -m pytest tests/ -q -n 8 --dist loadgroup
fi
warn "pytest-xdist not installed — serial suite (pip3 install pytest-xdist)"
exec python3 -m pytest tests/ -q
