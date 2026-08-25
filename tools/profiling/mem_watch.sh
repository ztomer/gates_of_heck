#!/bin/bash
# mem_watch.sh — Monitor CadGoose memory usage with leaks and heap sampling
#
# Usage:
#   ./mem_watch.sh                          # launch + monitor
#   ./mem_watch.sh --attach <PID>          # attach to running process
#   ./mem_watch.sh --attach <PID> --quick  # single snapshot (no loop)
#
# Saves: /tmp/cadgoose_mem_<timestamp>/

set -euo pipefail

# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
PROJECT_DIR="$(_goh_profile_target)"
BUILD_DIR="$PROJECT_DIR/build"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="/tmp/cadgoose_mem_${TIMESTAMP}"
QUICK=false

mkdir -p "$OUTPUT_DIR"

echo "=== CadGoose Memory Watch ==="
echo "Timestamp: $TIMESTAMP"
echo "Output: $OUTPUT_DIR"
echo ""

# Find or launch CadGoose
CADGOOSE_PID=""
if [ "${1:-}" = "--attach" ] && [ -n "${2:-}" ]; then
    CADGOOSE_PID="$2"
    echo "Attaching to PID $CADGOOSE_PID"
elif [ "${1:-}" = "--quick" ] || [ "${2:-}" = "--quick" ] || [ "${3:-}" = "--quick" ]; then
    QUICK=true
fi

if [ -z "$CADGOOSE_PID" ] && [ "$QUICK" = false ]; then
    if [ ! -f "$BUILD_DIR/CadGoose" ]; then
        echo "Building CadGoose..."
        (cd "$BUILD_DIR" && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(sysctl -n hw.logicalcpu) CadGoose)
    fi
    echo "Launching CadGoose..."
    "$BUILD_DIR/CadGoose" > /dev/null 2>&1 &
    CADGOOSE_PID=$!
    sleep 3
    if ! kill -0 "$CADGOOSE_PID" 2>/dev/null; then
        echo "ERROR: CadGoose failed to start"
        exit 1
    fi
    echo "Launched with PID $CADGOOSE_PID"
fi

if [ -n "$CADGOOSE_PID" ]; then
    echo ""
    echo "--- Initial snapshot ---" | tee -a "$OUTPUT_DIR/summary.txt"
    ps -o pid,rss,vsz,cpu,comm -p "$CADGOOSE_PID" >> "$OUTPUT_DIR/summary.txt" 2>/dev/null
    ps -o pid,rss,vsz,cpu,comm -p "$CADGOOSE_PID" 2>/dev/null

    # leaks initial
    leaks "$CADGOOSE_PID" > "$OUTPUT_DIR/leaks_initial.txt" 2>&1 || true
    echo "leaks: $(grep -c "leak" "$OUTPUT_DIR/leaks_initial.txt" 2>/dev/null || echo 0) potential leaks"

    # heap initial
    heap "$CADGOOSE_PID" -sortBySize > "$OUTPUT_DIR/heap_initial.txt" 2>&1 || true
    echo "heap: $(wc -l < "$OUTPUT_DIR/heap_initial.txt") entries"

    # Save initial summary
    {
        echo "=== Initial State ==="
        echo "PID: $CADGOOSE_PID"
        echo "Date: $(date)"
        echo ""
        echo "--- RSS ---"
        ps -o rss= -p "$CADGOOSE_PID" 2>/dev/null
        echo ""
        echo "--- Top heap allocations ---"
        head -20 "$OUTPUT_DIR/heap_initial.txt"
        echo ""
        echo "--- Leaks ---"
        head -10 "$OUTPUT_DIR/leaks_initial.txt"
    } > "$OUTPUT_DIR/snapshot_initial.txt"

    if [ "$QUICK" = true ]; then
        echo ""
        echo "Quick snapshot saved to $OUTPUT_DIR"
        exit 0
    fi

    echo ""
    echo "Monitoring every 5 seconds. Press Ctrl+C to stop."
    echo ""

    # Monitoring loop
    SAMPLE=0
    trap 'echo ""; echo "Stopped."; break' INT
    while true; do
        sleep 5
        SAMPLE=$((SAMPLE + 1))

        if ! kill -0 "$CADGOOSE_PID" 2>/dev/null; then
            echo "ERROR: CadGoose crashed at sample $SAMPLE"
            echo "SAMPLE $SAMPLE: CRASH" >> "$OUTPUT_DIR/summary.txt"
            break
        fi

        RSS=$(ps -o rss= -p "$CADGOOSE_PID" 2>/dev/null || echo "0")
        CPU=$(ps -o %cpu= -p "$CADGOOSE_PID" 2>/dev/null || echo "0")
        RSS_MB=$((RSS / 1024))
        echo "Sample $SAMPLE: RSS=${RSS_MB}MB CPU=${CPU}%"

        echo "SAMPLE $SAMPLE: RSS=${RSS}KB CPU=${CPU}%" >> "$OUTPUT_DIR/summary.txt"

        # Leaks every 5 samples (25s)
        if [ $((SAMPLE % 5)) -eq 0 ]; then
            leaks "$CADGOOSE_PID" > "$OUTPUT_DIR/leaks_${SAMPLE}.txt" 2>&1 || true
            LEAK_COUNT=$(grep -c "leak" "$OUTPUT_DIR/leaks_${SAMPLE}.txt" 2>/dev/null || echo 0)
            echo "  leaks: $LEAK_COUNT potential leaks"
        fi

        # Full heap dump every 12 samples (60s)
        if [ $((SAMPLE % 12)) -eq 0 ]; then
            echo "  collecting heap snapshot..."
            heap "$CADGOOSE_PID" -sortBySize > "$OUTPUT_DIR/heap_${SAMPLE}.txt" 2>&1 || true
        fi
    done
    trap - INT

    # Final snapshot
    echo ""
    echo "--- Final snapshot ---"
    RSS=$(ps -o rss= -p "$CADGOOSE_PID" 2>/dev/null || echo "terminated")
    echo "RSS: $RSS KB"
    {
        echo ""
        echo "=== Final State ==="
        echo "Date: $(date)"
        ps -o pid,rss,vsz,cpu,comm -p "$CADGOOSE_PID" 2>/dev/null || echo "Process terminated"
    } >> "$OUTPUT_DIR/summary.txt"

    echo ""
    echo "Results saved to: $OUTPUT_DIR"
    echo "Summary: $OUTPUT_DIR/summary.txt"
else
    echo "Usage:"
    echo "  $0                          # launch + monitor"
    echo "  $0 --attach <PID>           # attach to running process"
    echo "  $0 --attach <PID> --quick   # single snapshot"
fi
