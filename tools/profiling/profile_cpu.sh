# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
#!/bin/bash
# profile_cpu.sh - CPU profiling with Time Profiler

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
    xctrace analyze --detailed "$TRACE_FILE" 2>/dev/null | head -30 || \
    echo "Profile saved to: $TRACE_FILE"
    echo "Open in Instruments: open \"$TRACE_FILE\""
else
    echo "Error: Profile failed"
    exit 1
fi