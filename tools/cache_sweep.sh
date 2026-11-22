#!/usr/bin/env bash
# cache_sweep.sh -- the half-hourly job: the ONE evictor's sweep over the build caches (GOH.CACHEEVICT).
#
# An unattended job runs the COMMITTED code, never the working tree (gates/_from_head.sh, C4).
# Always `run`: the job exists to act; a dry run is `goh cache-sweep plan`. Every object a live
# build holds is left alone (lib/cache_evict.py `held`), so the schedule needs no idle window of
# its own. The log launchd hands it is bounded here: past 256 KiB it moves to <log>.1 first.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log="${HOME:-/nonexistent}/Library/Logs/goh-cache-sweep.log"
if [ -f "$log" ] && [ "$(wc -c <"$log")" -gt 262144 ]; then mv -f "$log" "$log.1"; fi
date -u +%Y-%m-%dT%H:%M:%SZ
exec bash "$HERE/../gates/python.sh" "$HERE/../lib/cache_sweep.py" run "$@"
exit
} # parse-guard
