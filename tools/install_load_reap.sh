#!/usr/bin/env bash
# install_load_reap.sh [install|remove|check] -- the minute job that kills orphaned load spinners.
# The logic is tools/install_launchagent.sh's; this names the job. It does not fight the reclaim
# jobs (com.ztomer.reclaim-build-root, com.ztomer.reclaim-build-space): a different label, a
# different script, and this one only ever touches the pids its own registry names.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
exec "$(dirname "${BASH_SOURCE[0]}")/install_launchagent.sh" com.ztomer.goh-load-reap "$@"
exit
} # parse-guard
