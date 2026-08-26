#!/usr/bin/env bash
# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
# profile_memory.sh - Memory profiling with Allocations instrument

DURATION=${2:-30}
PID=$1

if [ -z "$PID" ]; then
    PID=$(pgrep -f "CadGoose" | head -1)
fi

if [ -z "$PID" ]; then
    echo "Error: CadGoose not running. Pass PID or start CadGoose first."
    exit 1
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
OUTPUT_DIR="$(_goh_profile_target)/tools/profiling"
TRACE_FILE="${OUTPUT_DIR}/memory_profile_${TIMESTAMP}.trace"

echo "Profiling Memory for $DURATION seconds (PID: $PID)..."
echo "Saving to: $TRACE_FILE"

xctrace record \
    --template "Allocations" \
    --duration $DURATION \
    --pid $PID \
    --output "$TRACE_FILE" \
    2>&1

if [ -f "$TRACE_FILE" ]; then
    echo ""
    echo "Profile saved to: $TRACE_FILE"
    echo "Open in Instruments: open \"$TRACE_FILE\""
    echo ""
    echo "Note: Use Instruments app to analyze detailed allocation patterns"
else
    echo "Error: Profile failed"
    exit 1
fi
