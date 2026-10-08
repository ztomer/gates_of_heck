#!/usr/bin/env bash
# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
# profile_cpu.sh - CPU profiling with Time Profiler

DURATION=${2:-30}
PID=$1

if [ -z "$PID" ]; then
    # Read whole, then the first: `pgrep | head -1` is a race under a caller's pipefail
    # (goh early-exit-pipe).
    pids="$(pgrep -f "CadGoose" || true)"
    PID="$(head -n1 <<<"$pids")"
fi

if [ -z "$PID" ]; then
    echo "Error: CadGoose not running. Pass PID or start CadGoose first."
    exit 1
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
OUTPUT_DIR="$(_goh_profile_target)/tools/profiling"
TRACE_FILE="${OUTPUT_DIR}/cpu_profile_${TIMESTAMP}.trace"

echo "Profiling CPU for $DURATION seconds (PID: $PID)..."
echo "Saving to: $TRACE_FILE"

xctrace record \
    --template "Time Profiler" \
    --duration $DURATION \
    --pid $PID \
    --output "$TRACE_FILE" \
    2>&1

if [ -f "$TRACE_FILE" ]; then
    echo ""
    echo "=== Top CPU Consumers ==="
    # The analysis is read whole: `xctrace | head -30 || echo` let head's early exit decide the
    # branch (goh early-exit-pipe).
    if analysis="$(xctrace analyze --detailed "$TRACE_FILE" 2>/dev/null)"; then
        head -30 <<<"$analysis"
    else
        echo "Profile saved to: $TRACE_FILE"
    fi
    echo "Open in Instruments: open \"$TRACE_FILE\""
else
    echo "Error: Profile failed"
    exit 1
fi
