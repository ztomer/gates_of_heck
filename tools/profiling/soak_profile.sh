# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
#!/bin/bash
# soak_profile.sh - Long duration soak test with profiling

DURATION_MINUTES=${1:-30}
DURATION_SECONDS=$((DURATION_MINUTES * 60))
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
OUTPUT_DIR="$(_goh_profile_target)/tools/profiling"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_FILE="${OUTPUT_DIR}/soak_results_${TIMESTAMP}.md"
CADGOOSE_PID=""

echo "=== CadGoose ${DURATION_MINUTES}-minute Soak Test ==="
echo "Started at: $(date)"
echo "Results will be saved to: $RESULTS_FILE"
echo ""

# Start CadGoose if not running
if ! pgrep -f "CadGoose" > /dev/null; then
    echo "Starting CadGoose..."
    cd "$OUTPUT_DIR"
    ./build/CadGoose > /tmp/cadgoose_soak.log 2>&1 &
    sleep 3
fi

CADGOOSE_PID=$(pgrep -f "CadGoose" | head -1)
if [ -z "$CADGOOSE_PID" ]; then
    echo "Error: Could not start CadGoose"
    exit 1
fi

echo "CadGoose running with PID: $CADGOOSE_PID"
echo ""

# Collect initial state
cat > "$RESULTS_FILE" << EOF
# CadGoose Soak Test Results

**Test Date:** $(date)
**Duration:** ${DURATION_MINUTES} minutes
**PID:** ${CADGOOSE_PID}

## Initial State

EOF

echo "Initial Memory:" >> "$RESULTS_FILE"
ps -o pid,rss,vsz,comm -p $CADGOOSE_PID >> "$RESULTS_FILE" 2>/dev/null
echo "" >> "$RESULTS_FILE"

# Sample memory every 60 seconds
SAMPLE_COUNT=0
TOTAL_SAMPLES=$((DURATION_MINUTES))

echo "Running soak test - collecting $TOTAL_SAMPLES samples..."

while [ $SAMPLE_COUNT -lt $TOTAL_SAMPLES ]; do
    sleep 60
    SAMPLE_COUNT=$((SAMPLE_COUNT + 1))

    # Check if still running
    if ! kill -0 $CADGOOSE_PID 2>/dev/null; then
        echo "ERROR: CadGoose crashed at sample $SAMPLE_COUNT"
        echo "## Crash at sample $SAMPLE_COUNT" >> "$RESULTS_FILE"
        break
    fi

    # Memory sample
    MEM=$(ps -o rss= -p $CADGOOSE_PID 2>/dev/null)
    CPU=$(ps -o %cpu= -p $CADGOOSE_PID 2>/dev/null)

    echo "Sample $SAMPLE_COUNT/$TOTAL_SAMPLES: RSS=${MEM}KB CPU=${CPU}%" | tee -a "$RESULTS_FILE"

    # Periodic CPU profile (every 5 minutes)
    if [ $((SAMPLE_COUNT % 5)) -eq 0 ]; then
        echo "  -> Taking CPU sample..." | tee -a "$RESULTS_FILE"
        PROFILE_FILE="${OUTPUT_DIR}/soak_cpu_${TIMESTAMP}_${SAMPLE_COUNT}.trace"
        timeout 15 xctrace record --template "Time Profiler" --pid $CADGOOSE_PID --output "$PROFILE_FILE" 2>/dev/null || true
    fi
done

echo ""
echo "Soak test complete!"
echo ""

# Final state
echo "## Final State" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"
echo "Final Memory:" >> "$RESULTS_FILE"
ps -o pid,rss,vsz,comm -p $CADGOOSE_PID >> "$RESULTS_FILE" 2>/dev/null
echo "" >> "$RESULTS_FILE"

# System info
echo "## System Info" >> "$RESULTS_FILE"
echo "macOS Version: $(sw_vers -productVersion)" >> "$RESULTS_FILE"
echo "Hardware: $(sysctl -n hw.model)" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"

# Tail the log
echo "## Recent Log Entries" >> "$RESULTS_FILE"
echo '```' >> "$RESULTS_FILE"
tail -30 /tmp/cadgoose_soak.log >> "$RESULTS_FILE" 2>/dev/null
echo '```' >> "$RESULTS_FILE"

echo ""
echo "Results saved to: $RESULTS_FILE"
echo ""
echo "To open in Xcode/Instruments:"
echo "  open ${OUTPUT_DIR}/soak_cpu_${TIMESTAMP}_*.trace"
echo ""
echo "Done at: $(date)"