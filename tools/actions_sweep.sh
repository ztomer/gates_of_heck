#!/usr/bin/env bash
# actions_sweep.sh -- the daily job: sweep GitHub Actions storage for every repo (G14).
#
# An unattended job runs the COMMITTED code, never the working tree (the build-root pressure job
# applied an uncommitted change within minutes of the edit, 2026-10-17): this re-runs itself from
# an export of HEAD (gates/_from_head.sh, C4). Always --apply: the job exists to act, and a dry run
# is `python3 scripts/actions_sweep.py`.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../gates/python.sh" "$HERE/../scripts/actions_sweep.py" --apply "$@"
exit
} # parse-guard
