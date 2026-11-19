# shellcheck shell=bash
# lib/timings_hold.sh -- a gate that RUNS TESTS keeps the step profile (GOH_TIMINGS, lib/step_timings.py)
# for its own rows and hands the tests none (GOH.TIMINGSBLEED). Sourced, never run.
#
# v0.139.4's release profile held 1475 rows, 1068 of them the suite's fixtures: the coverage step ran
# the tests with the release's GOH_TIMINGS inherited, and every fixture gate a test started appended
# its steps to the ship's file. tests/conftest.py drops the profile for pytest; a Rust test, a ctest
# or a consumer's suite has no such hook, so the gate that starts them drops it.
#   goh_timings_hold      -- un-export the profile; its values kept in goh_timings_held (VAR=value ...)
#   env "${goh_timings_held[@]}" <cmd>   -- one of the gate's OWN rows, under the step it belongs to
#   goh_timings_unbind <array>           -- append `-u VAR` for each profile variable, for `env`
goh_timings_vars=(GOH_TIMINGS GOH_TIMINGS_PARENT GOH_TIMINGS_KIND GOH_TIMINGS_CWD)
goh_timings_hold() {
    local v
    goh_timings_held=()
    for v in "${goh_timings_vars[@]}"; do
        [ -z "${!v:-}" ] || goh_timings_held+=("$v=${!v}")
        unset "$v"
    done
}
goh_timings_unbind() {
    local -n _goh_unbind="$1"
    local v
    for v in "${goh_timings_vars[@]}"; do _goh_unbind+=(-u "$v"); done
}
