#!/usr/bin/env bash
# install_cache_sweep.sh [install|remove|check] -- the half-hourly job that runs the ONE cache
# evictor (tools/cache_sweep.sh, GOH.CACHEEVICT). The logic is tools/install_launchagent.sh's.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
exec "$(dirname "${BASH_SOURCE[0]}")/install_launchagent.sh" com.ztomer.goh-cache-sweep "$@"
exit
} # parse-guard
