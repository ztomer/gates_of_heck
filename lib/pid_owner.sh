# shellcheck shell=bash
# pid_owner.sh -- sourced, never run. The shell half of lib/pid_owner.py (GOH.STRAYSIGNAL): signal a
# SAVED pid only while it still names the process it was saved for.
#
# A pid kept in a variable is reused once its process dies, and bash reaps a background child the
# moment it exits (its SIGCHLD handler), so even `kill "$!"` names a stranger once the child has
# ended and the pid space has wrapped -- minutes on a loaded machine (430 pids/s at load 17). So a
# pid is kept as a STAMP, `<pid> <start time>` (`ps -o lstart=`, whitespace-collapsed), taken while
# the process is known to be ours (right after `$!`), and signalled through `goh_pid_kill`, which
# re-reads the start time and refuses (status 1) when the pid is gone or somebody else's. The start
# time has a one-second resolution: a wrap of the whole pid space inside one second is the stated
# limit. checks/check_saved_pid_kill.py refuses a bare `kill "$var"`; tests/test_pid_owner.py.

_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# `<pid> <start time>` of a live process, or nothing (status 1) when there is none to stamp.
goh_pid_stamp() {
    local raw
    case "${1:-}" in '' | *[!0-9]*) return 1 ;; esac
    raw="$(ps -o lstart= -p "$1" 2>/dev/null || true)"
    # shellcheck disable=SC2086 # the split collapses ps's padding
    set -- "$1" $raw
    [ "$#" -gt 1 ] || return 1
    printf '%s\n' "$*"
}

# goh_pid_kill SIGNAL STAMP: send SIGNAL (a name or number, no dash) to the stamped process if the
# pid still names it. Status 0 when sent, 1 when refused or the kernel refused.
goh_pid_kill() {
    local sig="$1" stamp="${2:-}" now
    [ -n "$stamp" ] || return 1
    now="$(goh_pid_stamp "${stamp%% *}")" || return 1
    [ "$now" = "$stamp" ] || return 1
    kill "-$sig" "${stamp%% *}" 2>/dev/null
}
