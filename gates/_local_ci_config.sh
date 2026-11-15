# _local_ci_config.sh -- what gates/local_ci.sh runs, and under what ceiling: the step list and
# the per-step limit, read from the repo's .gatesrc and the command line. Split out of local_ci.sh
# by concern when that file stood at the 500-line cap (GOH.LOCALCISPLIT); local_ci.sh is the
# runner (the slot, the scheduler, the report) and this is its configuration. Sourced, never run.
# shellcheck shell=bash
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# GOH_LCI_TIMEOUT: per-step wall-clock ceiling in seconds. The DEFAULT is 900 (15 min), and the
# default is the point: this was unset everywhere in the estate, which means "no limit", which means
# a hung `cargo test` sat there until something killed it -- and the thing that killed it (a timeout,
# a cancelled agent) killed the run BEFORE it could report anything, so the only observable was
# silence. A private server workspace 2026-10-03: nine orphans, each holding the cargo build lock,
# every later `cargo test` blocked, and nothing anywhere said why. A ceiling nobody gave a number is
# a suggestion. 900 is set from measurement, not taste: this estate's own slowest test step is
# mediaops' rust gate at 168 s and the whole pytest suite is 76 s, so 900 is 5x the slowest thing
# measured and a fifteenth of the day it cost.
#
# `0` is the explicit escape hatch and now has to be typed.
#
# THE SWEEP IS THE OTHER HALF, and it was measured wrong. The old sweep was `pkill -P "$pid"`,
# DIRECT children only, on a step shaped `cargo test` -> test binary -> the server the test spawned.
# Measured 2026-10-03 with `bash -c 'sleep 400 & wait'`: the old sweep left 2 grandchildren ALIVE;
# `lib/bounded_run.py` (own session + killpg) leaves NOTHING. So the mechanism meant to unstick a
# hung step left running exactly the processes that make the NEXT step hang.
# lci_limit -- prints the ceiling; a bad value dies (in the caller's $(...), so it checks the exit).
lci_limit() {
    case "${GOH_LCI_TIMEOUT:-900}" in
        ""|0) echo 0 ;;
        *[!0-9]*)
            die "GOH_LCI_TIMEOUT must be a non-negative integer of seconds (got '${GOH_LCI_TIMEOUT}')" ;;
        # `:-900` on BOTH reads, not just the subject. The case matched the defaulted value and the arm
        # then read the bare name, so a repo that never set the key died on an unbound variable under
        # `set -u` -- the default being on is what made it reachable. (The key was unset in every repo
        # in the estate until now, and the branch was simply never taken.)
        *) echo "${GOH_LCI_TIMEOUT:-900}" ;;
    esac
}

# THE STEP LIST: newline-joined "source<TAB>cmd" records in ALL_STEPS, counted in _n -- .gatesrc's
# GOH_CI_STEPS (SRC_GATESRC) first, then each --step (CLI_STEPS), colon-separated in both.
_lci_add_all() { # _lci_add_all <source> <colon-joined steps>
    local rest="$2" tok
    while [ -n "$rest" ]; do
        tok="${rest%%:*}"
        rest="${rest#*:}"
        [ -n "$tok" ] && { _n=$((_n + 1)); ALL_STEPS="${ALL_STEPS}${ALL_STEPS:+
}$1	$tok"; }
        [ "$tok" = "$rest" ] && break
    done
    return 0
}

lci_collect_steps() {
    ALL_STEPS=""
    _n=0
    # THE MARK: push_gate.sh reads it to tell whether the repo's tools/gate.sh ran the steps its
    # .gatesrc declares, and runs them itself when it did not (tests/test_push_gate_ci_steps.py).
    # The tree, so a local_ci run inside some other repo does not stand in for this one's.
    if [ -n "$SRC_GATESRC" ] && [ -n "${GOH_CI_STEPS_MARK:-}" ]; then
        printf '%s\n' "$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$ROOT")" \
            >>"$GOH_CI_STEPS_MARK"
    fi
    _lci_add_all ".gatesrc" "$SRC_GATESRC"
    _lci_add_all "--step" "$CLI_STEPS"
}
