#!/usr/bin/env bash
# install_actions_sweep.sh [install|remove|check] -- the daily job that sweeps GitHub Actions storage.
# The logic is tools/install_launchagent.sh's; this names the job (scripts/actions_sweep.py, G14).
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
exec "$(dirname "${BASH_SOURCE[0]}")/install_launchagent.sh" com.ztomer.goh-actions-sweep "$@"
exit
} # parse-guard
