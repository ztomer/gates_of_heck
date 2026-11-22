# shellcheck shell=bash disable=SC2034,SC2154  # ship_timings: read by tools/land.sh, which sources this; pre_*: its land_pre's
# lib/land_row.sh -- sourced by tools/land.sh (GOH_PY, GOH); pinned by tests/test_ship_profile.py.
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# THE LAND'S OWN ROWS (GOH.RELEASELANDGATE): under a caller's profile (goh ship's GOH_TIMINGS, the
# release kit's "release: land" row) the pre-step and the gate are rows of their own, and what they
# run nests under them, so lib/step_attribution.py can name the seconds: v0.139.4's land row was
# 160 s, its rows 6 s, the rest a pre-step pytest that wrote none. A land with no caller profile
# (the queue's, whose gate rows lib/land_profile.py reads flat) is profiled as before.
ship_timings="${GOH_TIMINGS:-}"
# land_row LABEL T0 RC -- one row for a part of this land since T0 (EPOCHREALTIME), caller's profile only.
land_row() {
    [ -n "$ship_timings" ] || return 0
    local ms
    ms="$(awk -v a="$2" -v b="${EPOCHREALTIME/,/.}" 'BEGIN { printf "%.1f", (b - a) * 1000 }')"
    GOH_TIMINGS="$ship_timings" "$GOH_PY" "$GOH/lib/step_timings.py" record "$1" "$ms" "$3" phase 7>&- 8<&- </dev/null || true
}
# land_pre_took WAIT RUN -- THE FIGURES IN EVERY OUTCOME (GOH.PREWIDTH): the land record's `pre_run`
# was read back from the green line alone (lib/land_spd.py PRE_TOOK), so a pre-step with no verdict
# left it null. The enqueue names a file (GOH_LAND_PRE_TOOK, lib/land_prestep.py); cpu_s is the
# pytest tree's (lib/pre_cpu.py), 0 when no test ran; workers 0 when the step never started.
land_pre_took() {
    local w="${pre_workers:-0}" l="${pre_limit:-0}" verdict=green
    pre_cpu_s=0
    [ ! -s "$pre_start.cpu" ] || IFS= read -r pre_cpu_s <"$pre_start.cpu" || true
    [[ "$pre_cpu_s" =~ ^[0-9]+(\.[0-9]+)?$ ]] || pre_cpu_s=0
    [[ "$w" =~ ^[0-9]+$ ]] || w=0
    [[ "$l" =~ ^[0-9]+$ ]] || l=0
    [ "$pre_rc" -eq 0 ] || verdict=red
    [ "$pre_rc" -ne 124 ] || verdict=none
    [ -z "${GOH_LAND_PRE_TOOK:-}" ] || printf '{"wait_s": %d, "run_s": %d, "limit_s": %d, "workers": %d, "cpu_s": %s, "verdict": "%s"}\n' \
        "$1" "$2" "$l" "$w" "$pre_cpu_s" "$verdict" >"$GOH_LAND_PRE_TOOK" || true
}
