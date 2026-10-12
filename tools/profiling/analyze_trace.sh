#!/bin/bash
# analyze_trace.sh — rank a Time Profiler .trace's hotspots into a text report
#
# Usage:
#   ./analyze_trace.sh <trace_file.trace> [output_report.txt] [--process NAME] [--under FRAME --tree N]
#
# Exports the trace's `time-profile` table (the one today's xctrace writes) beside the trace and
# ranks it with xctrace_profile.py: by thread, by the leaf's binary, self and inclusive. --process
# keeps the processes whose name contains NAME (an all-processes recording holds every one).
# Report: <trace>.hotspots.txt unless named. Requires xcrun xctrace (Xcode) and python3.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=tui/lib.sh
. "$HERE/../../tui/lib.sh"

TRACE_FILE="${1:-}"
[ -n "$TRACE_FILE" ] || die "usage: $0 <trace_file.trace> [output_report.txt] [--process NAME] [--under FRAME --tree N]" 64
[ -e "$TRACE_FILE" ] || die "trace not found: $TRACE_FILE"
shift
REPORT="${TRACE_FILE%.trace}.hotspots.txt"
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then REPORT="$1"; shift; fi
XML="${TRACE_FILE%.trace}.time-profile.xml"

info "exporting the time-profile table of $TRACE_FILE"
xcrun xctrace export --input "$TRACE_FILE" --output "$XML" \
    --xpath '/trace-toc/run[@number="1"]/data/table[@schema="time-profile"]' \
    || die "xctrace export failed: open the trace in Instruments (open \"$TRACE_FILE\")"

info "ranking"
if ! python3 "$HERE/xctrace_profile.py" "$XML" "$@" > "$REPORT"; then
    cat "$REPORT"
    die "no samples matched in $XML"
fi
head -40 "$REPORT"
ok "report: $REPORT (Instruments: open \"$TRACE_FILE\")"
