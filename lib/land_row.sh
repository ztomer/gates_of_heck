# shellcheck shell=bash disable=SC2034  # ship_timings: read by tools/land.sh, which sources this
# lib/land_row.sh -- sourced by tools/land.sh (GOH_PY, GOH); pinned by tests/test_ship_profile.py.
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
