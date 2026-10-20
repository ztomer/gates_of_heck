#!/usr/bin/env bash
# load_reap.sh -- the minute job that kills the load spinners whose owner is gone.
#
# An unattended job runs the COMMITTED code, never the working tree: the build-root pressure job
# applied an uncommitted reclaimer change within minutes of the edit (2026-10-17). So this
# re-runs itself from an export of HEAD (gates/_from_head.sh, C4) and the export's copy of the
# reaper with it.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../gates/python.sh" "$HERE/load_reap.py" "$@"
exit
} # parse-guard
