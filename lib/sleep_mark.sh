# shellcheck shell=bash
# lib/sleep_mark.sh -- did the machine sleep during a gate run? Sourced by gates/push_gate.sh and
# gates/round.sh after tui/lib.sh (it prints through `warn`).

# goh_sleep_mark — when the machine last went to sleep, in epoch seconds, or nothing where the
# OS does not say (macOS's kern.sleeptime; Linux has no such counter, so it is blank there).
# GOH_SLEEPTIME_FILE, when set, is read instead: the seam the tests plant a sleep through.
goh_sleep_mark() {
    if [ -n "${GOH_SLEEPTIME_FILE:-}" ]; then
        cat "$GOH_SLEEPTIME_FILE" 2>/dev/null
        return 0
    fi
    sysctl -n kern.sleeptime 2>/dev/null | sed -n 's/^{ sec = \([0-9][0-9]*\),.*/\1/p'
}

# goh_slept_since <mark> — when a run that took <mark> at its start fails, say so if the machine
# slept during it. A suite run in a dark wake has no display and a stalled clock, so a test that
# reads a screen, the GPU or elapsed time fails for the SLEEP, and the red reads exactly like a
# code defect (ZoneWM, 2026-10-11: two Swift rows red at the second of a dark wake, green awake).
# Names the sleep and returns 0; returns 1 when there was none or the OS cannot say.
goh_slept_since() {
    local mark="$1" now
    now="$(goh_sleep_mark)"
    [ -n "$mark" ] && [ -n "$now" ] && [ "$now" -gt "$mark" ] || return 1
    warn "the machine SLEPT during this run (last sleep $(date -r "$now" 2>/dev/null || date -d "@$now")):"
    warn "  a test reading a display, the GPU or the clock fails in a dark wake; re-run awake"
    warn "  before reading the failures above as defects"
    return 0
}
