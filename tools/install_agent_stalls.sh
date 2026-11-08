#!/usr/bin/env bash
# install_agent_stalls.sh [install|remove|check] -- the five-minute job that tells the owner of a
# subagent stalled on background jobs that are gone (GOH.STALLNOTIFY). The logic is
# tools/install_launchagent.sh's; this names the job.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
exec "$(dirname "${BASH_SOURCE[0]}")/install_launchagent.sh" com.ztomer.goh-agent-stalls "$@"
exit
} # parse-guard
