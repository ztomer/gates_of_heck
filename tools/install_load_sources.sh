#!/usr/bin/env bash
# install_load_sources.sh [install|remove|check] -- the job that samples what burns the CPU, by
# process family and repo, into the load-sources ledger (`goh load --report`, GOH.LOADSOURCES).
# The logic is tools/install_launchagent.sh's; this names the job.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
exec "$(dirname "${BASH_SOURCE[0]}")/install_launchagent.sh" com.ztomer.goh-load-sources "$@"
exit
} # parse-guard
