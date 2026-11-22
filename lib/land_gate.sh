# shellcheck shell=bash disable=SC2154,SC2034  # verdicts, tip, ship_timings, land_timings, ship_full: land.sh's, set before this is sourced; suite_why: its ledger field
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# lib/land_gate.sh -- the land's gate run, sourced by tools/land.sh (GOH_PY, GOH, tip, verdicts,
# land_timings, ship_timings, land_row); pinned by tests/test_land_prewait.py and tests/test_land.py.
# Moved out of tools/land.sh whole (GOH.PREWAITVERDICT) to keep it under the headroom line.
# The gate runs under tools/load.py with 0 spinners, which admits it by a gate slot -- the one
# admission every gate takes, so a land and a push gate wait for the same slots (GOH.ONEADMISSION,
# docs/decisions/D-0008) -- taken HERE, under the land lock, never around this script: a land queued
# on the lock then holds nothing a ship holding the lock can be waiting on (GOH.LOCKORDER: land 20
# before gate_slot 50). 90 minutes bounds the gate as the coordinator's wrapper did (GOH_LAND_GATE_MAX,
# a seam a test shortens: a gate that hangs dies at it, red, GOH.GATEPARALLEL). Class `land`
# (GOH_GATE_CLASS, consumed by lib/admission.py): ahead of every other long waiter (GOH.SLOTPRIORITY).
# GOH_TIMING_VIA: the push gate this runs is this land's, so it leaves the ledger row to land.sh.
# land_gate ROOT WHOSE GATE -- the gate (empty: ROOT's push gate) under ROOT's gates; red exits 1.
land_gate() {
    local cmd="${3:-$1/tools/gate_profile.sh .}"
    info "gate: $cmd (under $2's gates)"
    # An export's gates are put first; a consumer's land (ROOT is the kit) keeps the caller's GOH_DIR
    # and PYTHONPATH, the ones the push after it runs under: forced to the kit, the proof keyed roots
    # the push's key did not, and the push re-ran what the land had proven (GOH.PRESTEPHEAD's
    # 8150234a; tests/test_proven.py::test_land_leaves_a_proof_the_push_that_follows_reuses).
    local over=()
    [ "$1" = "$GOH" ] || over=(GOH_DIR="$1" PYTHONPATH="$(goh_path_front PYTHONPATH "$1"; printf %s "$PYTHONPATH")")
    [ -z "$ship_timings" ] || over+=(GOH_TIMINGS_PARENT="land: gate")
    # COVERAGE IS THE SHIP'S (GOH.COVOFFLAND): where the ship makes the instrumented full run and
    # judges every floor over what landed since the last ship (lib/ship_full_run.py), the gate runs
    # the coverage step's tests plain (gates/rust_gate.sh GOH_RUST_COVERAGE=plain): 493-781 s of
    # instrumented suite was 69-76% of a goh land's gate (2026-11-19).
    [ "${ship_full:-}" != on ] || over+=(GOH_RUST_COVERAGE=plain)
    local g0="${EPOCHREALTIME/,/.}" grc=0 gate_wait=() gate_mark gate_t0
    gate_t0=$(date +%s)
    [ -z "${GOH_LAND_GATE_WAIT:-}" ] || gate_wait=(--wait-max "$GOH_LAND_GATE_WAIT")
    gate_mark="$(mktemp "$(goh_tmp)/goh-land-gatestart.XXXXXX")" || gate_mark=""
    [ -z "$gate_mark" ] || : >"$gate_mark"
    env ${over[@]+"${over[@]}"} GOH_PROFILE_PROVEN="${GOH_PROFILE_PROVEN-1}" GOH_LAND_VERDICTS_REUSE="$verdicts" \
            GOH_GATE_CLASS=land GOH_TIMING_VIA=land GOH_TIMINGS="${GOH_TIMINGS:-$land_timings}" \
            "$GOH_PY" "$1/tools/load.py" ${gate_wait[@]+"${gate_wait[@]}"} --background-ok "land gate" \
            --max "${GOH_LAND_GATE_MAX:-90m}" 0 -- bash -c 'date +%s >"$1" 2>/dev/null; exec bash -c "$2"' gate "${gate_mark:-/dev/null}" "$cmd" \
            7>&- 8<&- </dev/null || grc=$?
    land_row "land: gate" "$g0" "$grc"
    # A wait that expired (load.py's EXIT_BUSY, 3, and the gate never stamped its start) judged
    # nothing: no verdict, never "the gate failed" (GOH.PREWAITVERDICT). 75 is load.py's EXIT_GATE,
    # EX_TEMPFAIL: the lander reads it as no verdict and does not bisect a stack for it.
    if [ "$grc" -eq 3 ] && [ -n "$gate_mark" ] && [ ! -s "$gate_mark" ]; then
        rm -f "$gate_mark"
        err "land.sh: the gate never ran on ${tip:0:8}: waited $(($(date +%s) - ${gate_t0:-$(date +%s)})) s for a gate slot (GOH_LAND_GATE_WAIT), no verdict -- nothing landed; land again"
        exit 75
    fi
    [ -z "$gate_mark" ] || rm -f "$gate_mark"
    if [ "$grc" -ne 0 ]; then
        err "land.sh: the gate failed on ${tip:0:8} under $2's gates -- nothing landed"
        exit 1
    fi
}

# land_suite_why -- EVERY REUSE DECISION RECORDED (GOH.SUITEONCEMISS): why the gate's suite took or
# missed the enqueue's verdict (tools/pytest.sh `<record>.why`, a line per gate run), or why it never
# asked; logged, and kept for the land's ledger row (`suite_why`, beside the pre-land's `pre_why`).
land_suite_why() {
    if [ ! -s "${verdicts:-}" ]; then
        suite_why="missed: no kept record from the enqueue"
    elif [ -s "$verdicts.why" ]; then
        suite_why="$(paste -sd ';' "$verdicts.why")" || suite_why=""
    else
        suite_why="missed: the gate ran no whole suite to ask (tools/pytest.sh with no arguments)"
    fi
    info "suite: $suite_why"
}
