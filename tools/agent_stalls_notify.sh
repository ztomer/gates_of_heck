#!/usr/bin/env bash
# agent_stalls_notify.sh -- the five-minute job that tells the owner of a subagent nothing will wake.
#
# A coordinator wakes on a task-notification, a ScheduleWakeup or an owner prompt, and each fires
# its UserPromptSubmit hook, which names stalled subagents (probed, GOH.STALLNOTIFY). A coordinator
# whose every agent is stalled on background jobs that are gone never wakes, so this pass tells the
# OWNER: one macOS notification per such stall, once (hooks/claude/agent_stalls.py --notify). It is
# a one-shot pass that exits; launchd runs it again, so there is no daemon to outlive anyone.
#
# An unattended job runs the COMMITTED code, never the working tree (gates/_from_head.sh, C4).
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../gates/python.sh" "$HERE/../hooks/claude/agent_stalls.py" --notify \
    --log-path "${HOME:-/nonexistent}/Library/Logs/goh-agent-stalls.log" "$@"
exit
} # parse-guard
