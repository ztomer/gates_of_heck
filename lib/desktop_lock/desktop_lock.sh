#!/usr/bin/env bash
# desktop_lock.sh — machine-wide mutual exclusion for taking over the desktop.
#
# HOUSE LIB. Project-agnostic on purpose: any script on this Mac that seizes the
# real desktop takes THIS lock, at THIS path, so that unrelated projects and
# unrelated agent sessions exclude each other. A lock only excludes the peers
# that agree on its name, so the name lives here rather than in any one repo.
#
# WHAT COUNTS AS "TAKING OVER THE DESKTOP" -- take the lock if you do any of:
#   - drive the menu bar or the Accessibility tree (osascript / System Events)
#   - move or park the real cursor, or synthesize clicks and keystrokes
#   - call `screencapture`, or otherwise depend on what is frontmost
#   - launch a GUI app whose windows must be the ones you then act on
#   - DISRUPT the desktop even though you never read it: launch anything that
#     calls makeKeyAndOrderFront or claims a menu bar slot (it steals focus from
#     whoever IS mid-capture), or `pkill` an app by name (you may be closing the
#     lock owner's own copy out from under them)
#
# THAT LAST ONE IS THE CASE THAT ACTUALLY BITES, and the first four are worded
# from the point of view of a run that INTENDS to use the desktop, so it reads as
# not applying. A smoke test that launches an app and asserts on exit codes never
# touches a window and still takes the screen away from a peer mid-capture. The
# test is not "do I look at the desktop" but "can anyone tell that I ran".
#
# WHY IT IS A HARD LOCK. Two such runs at once do not merely produce a bad
# screenshot. They interleave clicks into each other's windows, and any run that
# also does the standard backup-mutate-restore dance on a config file will
# CORRUPT it: B backs up the state A already modified, A restores the pristine
# copy, then B restores A's test state over it. Both runs report success, the
# damage is to the user's real saved data, and nothing downstream can detect it.
#
# WHY `mkdir` AND NOT `flock`. macOS ships no flock(1). `mkdir` is atomic on
# every POSIX filesystem -- it either creates the directory or fails, with no
# window between the test and the create. A `[[ -e ]]` test followed by `mkdir`
# is NOT equivalent and races.
#
# WHY /tmp AND NOT $TMPDIR. The contended resource is the one physical desktop,
# shared by every checkout, worktree and agent session on this Mac. $TMPDIR is
# per-user on macOS and per-SESSION under some agent harnesses, which would
# silently hand each caller a private lock and no exclusion whatsoever. /tmp is
# machine-wide and stable, which is the property the lock's correctness rests on.
#
# DEADLOCK IS THE FAILURE MODE THIS FILE IS MOST CAREFUL ABOUT, because an agent
# session can die in more ways than it can exit cleanly. THREE independent
# releases, so no single failure can wedge the machine:
#
#   1. THE TRAP, on a clean exit, failure or Ctrl-C. Covers almost everything.
#   2. OWNER LIVENESS, for SIGKILL and for a crashed or force-quit agent, which
#      never run a trap at all. The owner PID is probed with `kill -0`.
#      PID ALONE IS NOT ENOUGH: PIDs are recycled, and a recycled PID reads as
#      "alive" forever, which is a permanent deadlock that looks like a busy
#      peer. So the owner's START TIME is recorded alongside the PID and must
#      also match -- a different process wearing the same number is not the owner.
#   3. A MAX HOLD AGE, for an owner that is alive but WEDGED (a hung osascript, a
#      dialog nobody will dismiss, an agent blocked on input that never comes).
#      Liveness cannot distinguish that from useful work, so age does: a run
#      still holding the desktop after DESKTOP_LOCK_MAX_HOLD has, by definition,
#      stopped being a run worth waiting for.
#
# The bias throughout is RECLAIM RATHER THAN WAIT. A wrongly-reclaimed lock costs
# one confused screenshot; a wrongly-held one costs every future run on the
# machine, including unattended scheduled ones.
#
# Requires the caller to have sourced gates_of_heck's tui/lib.sh (info/ok/warn/die).
#
# Usage:
#     source "$GOH_DIR/lib/desktop_lock/desktop_lock.sh"
#     trap 'desktop_lock_release' EXIT INT TERM
#     desktop_lock_acquire "necrohand gallery capture"
#
# RECORD FORMAT CONTRACT (consumers exist; do not change): $DESKTOP_LOCK_DIR is
# a directory whose `owner` file is exactly three lines --
#     <pid>
#     <process start time, whitespace-normalised>   (see _desktop_lock_start_time)
#     <human label> (pid <pid>)
# The Python half (desktop_lock.py) writes and reads the SAME bytes.

DESKTOP_LOCK_DIR="/tmp/mac-desktop-ui.lock"
# Long enough that a run queued behind a real peer waits it out rather than
# failing; short enough that a wedged harness reports instead of hanging a
# scheduled loop forever. Override per-caller when a run is legitimately longer.
DESKTOP_LOCK_TIMEOUT="${DESKTOP_LOCK_TIMEOUT:-300}"
# The wedged-owner ceiling (release #3). Well above any honest desktop run -- a
# full capture pass is under two minutes -- so exceeding it means stuck, not busy.
DESKTOP_LOCK_MAX_HOLD="${DESKTOP_LOCK_MAX_HOLD:-900}"
# Set only once actually acquired, so release is a no-op for a process that
# never held the lock and can be called unconditionally from an EXIT trap.
DESKTOP_LOCK_HELD=0

# The owner file is three lines: pid, the process start time, and a human label.
#
# DELIBERATELY NO `sed`. On this machine sed is rewritten out from under scripts
# -- an rtk Bash hook in some sessions, an `sd` alias in the interactive shell --
# and `sed -n 2p` comes back as "invalid value for --max-replacements". A lock
# whose staleness check silently returns EMPTY treats every live owner as dead
# and grants the lock to everyone, which is worse than having no lock at all
# because it still prints reassuring "acquired" messages. Pure bash has nothing
# to rewrite.
_desktop_lock_field() {
    local line n=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        n=$((n + 1))
        if [[ $n -eq $1 ]]; then printf '%s' "$line"; return 0; fi
    done < "$DESKTOP_LOCK_DIR/owner" 2>/dev/null
    return 1
}

# A process's start time, used to tell the real owner from a recycled PID.
# Empty when the process does not exist, which the caller treats as dead.
#
# THE NORMALISATION IS PART OF THE CROSS-LANGUAGE CONTRACT. desktop_lock.py must
# produce a byte-identical string for the same process, or each half reads the
# other's records as impostors and silently grants a lock the peer is holding --
# which is exactly what an untested first version did. `ps` pads single-digit
# days ("Aug  1" vs "Aug 18"), so anything short of collapsing whitespace runs
# drifts on two days in three. Word-splitting then rejoining with "$*" collapses
# runs AND trims the ends; Python's str.split() with no argument does the same,
# which is why both sides are written that way rather than with `tr -s`.
_desktop_lock_start_time() {
    local raw
    raw="$(ps -o lstart= -p "$1" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    set -- $raw
    printf '%s' "$*"
}

# A lock directory with no readable owner file is stale too: it means a run died
# between the mkdir and the write, leaving a lock nobody could ever release.
_desktop_lock_owner_alive() {
    [[ -r "$DESKTOP_LOCK_DIR/owner" ]] || return 1
    local pid recorded current
    pid="$(_desktop_lock_field 1)"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    # PID is live -- but is it the SAME process, or a recycled number?
    recorded="$(_desktop_lock_field 2)"
    current="$(_desktop_lock_start_time "$pid")"
    [[ -n "$recorded" && "$recorded" != "$current" ]] && return 1
    return 0
}

# Has a live owner been holding the desktop past any plausible run length?
_desktop_lock_expired() {
    local created now
    # The directory's own mtime is the creation time; no extra bookkeeping, and
    # it survives an owner that died before writing its owner file.
    created="$(stat -f %m "$DESKTOP_LOCK_DIR" 2>/dev/null || echo 0)"
    [[ "$created" == "0" ]] && return 1
    now="$(date +%s)"
    (( now - created >= DESKTOP_LOCK_MAX_HOLD ))
}

_desktop_lock_owner_label() {
    local label
    label="$(_desktop_lock_field 3)"
    [[ -n "$label" ]] && printf '%s' "$label" || printf 'an unknown run'
}

# Take the lock, waiting for any peer to finish. Dies rather than continuing
# unlocked -- continuing is the corrupting case this file exists to prevent.
desktop_lock_acquire() {
    local label="${1:-desktop run}"
    local waited=0
    local announced=0
    while ! mkdir "$DESKTOP_LOCK_DIR" 2>/dev/null; do
        if ! _desktop_lock_owner_alive; then
            warn "stale desktop lock from $( _desktop_lock_owner_label ) -- reclaiming"
            rm -rf "$DESKTOP_LOCK_DIR"
            continue
        fi
        if _desktop_lock_expired; then
            warn "desktop held past ${DESKTOP_LOCK_MAX_HOLD}s by $( _desktop_lock_owner_label ) -- wedged, reclaiming"
            rm -rf "$DESKTOP_LOCK_DIR"
            continue
        fi
        if [[ $announced -eq 0 ]]; then
            info "the desktop is held by $( _desktop_lock_owner_label )"
            step "waiting up to ${DESKTOP_LOCK_TIMEOUT}s rather than clicking into its windows"
            announced=1
        fi
        if [[ $waited -ge $DESKTOP_LOCK_TIMEOUT ]]; then
            die "desktop still held by $( _desktop_lock_owner_label ) after ${DESKTOP_LOCK_TIMEOUT}s"
        fi
        sleep 1
        waited=$((waited + 1))
    done
    printf '%s\n%s\n%s\n' \
        "$$" "$(_desktop_lock_start_time $$)" "$label (pid $$)" \
        > "$DESKTOP_LOCK_DIR/owner"
    DESKTOP_LOCK_HELD=1
    [[ $announced -eq 1 ]] && ok "desktop acquired after ${waited}s"
    return 0
}

# Release ONLY a lock this process actually holds. Without the guard, a run that
# died while waiting would delete the lock belonging to the peer it waited for.
desktop_lock_release() {
    [[ $DESKTOP_LOCK_HELD -eq 1 ]] || return 0
    DESKTOP_LOCK_HELD=0
    rm -rf "$DESKTOP_LOCK_DIR"
}
