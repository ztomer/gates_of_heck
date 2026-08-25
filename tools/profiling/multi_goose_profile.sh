#!/bin/bash
# multi_goose_profile.sh — Stress-profile with N geese
#
# Spawns N geese via the MCP command socket, then runs a 60-second Time Profiler
# trace. This exposes O(N²) separation-force and per-goose render hotspots that
# are invisible with a single goose.
#
# Usage:
#   ./tools/profiling/multi_goose_profile.sh [NUM_GEESE] [DURATION_SECONDS]
#
# Defaults: 5 geese, 60 seconds
#
# Exit codes:
#   0  = success
#   97 = build failure
#   98 = CadGoose failed to start / socket timeout

set -euo pipefail

# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
HERE="$(_goh_profile_here)"
PROJ="$(_goh_profile_target)"
BUILD_DIR="$PROJ/build"
SCRIPT_DIR="$PROJ/tools/profiling"
NUM_GEESE="${1:-5}"
DURATION="${2:-60}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="/tmp/multi_goose_profile_${TIMESTAMP}"
TRACE_FILE="$OUTPUT_DIR/profile.trace"
SOCKET="/tmp/desktop-goose-mcp.sock"
CADGOOSE_PID=""

mkdir -p "$OUTPUT_DIR"
exec > >(tee "$OUTPUT_DIR/run.log") 2>&1

cleanup() {
    if [ -n "$CADGOOSE_PID" ]; then
        kill "$CADGOOSE_PID" 2>/dev/null || true
        wait "$CADGOOSE_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "=============================================="
echo "  CadGoose Multi-Goose Hotspot Profile"
echo "  Geese:    $NUM_GEESE"
echo "  Duration: ${DURATION}s"
echo "  Output:   $OUTPUT_DIR"
echo "  Started:  $(date)"
echo "=============================================="
echo ""

# ── Step 1: Kill existing ─────────────────────────────────────────────────────
echo "[1/6] Killing any existing CadGoose..."
pkill -x "CadGoose" 2>/dev/null || true
sleep 1
rm -f "$SOCKET"
echo "  Done."

# ── Step 2: Build ─────────────────────────────────────────────────────────────
echo ""
echo "[2/6] Building CadGoose (Release)..."
cd "$BUILD_DIR"
cmake .. -DCMAKE_BUILD_TYPE=Release > /dev/null 2>&1 || true
if ! make -j"$(sysctl -n hw.logicalcpu)" CadGoose 2>&1 | tail -5; then
    echo "  FAIL: Build failed."
    exit 97
fi
echo "  Build OK."

# ── Step 3: Launch CadGoose ───────────────────────────────────────────────────
echo ""
echo "[3/6] Launching CadGoose..."
"$BUILD_DIR/CadGoose" > "$OUTPUT_DIR/cadgoose.log" 2>&1 &
CADGOOSE_PID=$!
echo "  PID: $CADGOOSE_PID"

SOCKET_OK=0
for i in $(seq 1 30); do
    if [ -S "$SOCKET" ]; then
        SOCKET_OK=1
        echo "  Socket ready after ${i}s"
        break
    fi
    kill -0 "$CADGOOSE_PID" 2>/dev/null || {
        echo "  FAIL: CadGoose died during startup."
        exit 98
    }
    sleep 1
done

if [ "$SOCKET_OK" -ne 1 ]; then
    echo "  FAIL: MCP socket not ready after 30s"
    exit 98
fi
sleep 3

# ── Step 4: Spawn extra geese ─────────────────────────────────────────────────
echo ""
echo "[4/6] Spawning $NUM_GEESE geese via MCP socket..."

EXTRA=$((NUM_GEESE - 1))  # 1 goose already running
SPAWNED=0

if [ "$EXTRA" -gt 0 ]; then
    for i in $(seq 1 "$EXTRA"); do
        # Send spawn command via Unix socket
        CMD='{"jsonrpc":"2.0","id":'"$i"',"method":"tools/call","params":{"name":"spawn_goose","arguments":{}}}'
        if echo "$CMD" | nc -U "$SOCKET" -w 2 > /dev/null 2>&1; then
            SPAWNED=$((SPAWNED + 1))
        else
            # Fallback: try socat
            if command -v socat > /dev/null 2>&1; then
                echo "$CMD" | socat - "UNIX-CONNECT:$SOCKET" > /dev/null 2>&1 && SPAWNED=$((SPAWNED + 1)) || true
            fi
        fi
        sleep 0.5
    done
fi

echo "  Spawned $SPAWNED extra geese (total: $((SPAWNED + 1)))."
echo "  Settling for 5s to let physics stabilize..."
sleep 5

# Snapshot memory before profiling
INIT_RSS=$(ps -o rss= -p "$CADGOOSE_PID" 2>/dev/null || echo "0")
echo "  RSS before profile: $((INIT_RSS / 1024)) MB"

# ── Step 5: Record trace ──────────────────────────────────────────────────────
echo ""
echo "[5/6] Recording ${DURATION}s Time Profiler trace..."
echo "  Trace: $TRACE_FILE"

xctrace record \
    --template "Time Profiler" \
    --duration "$DURATION" \
    --pid "$CADGOOSE_PID" \
    --output "$TRACE_FILE" \
    2>&1 | tail -5

if [ ! -e "$TRACE_FILE" ]; then
    echo "  FAIL: Trace file not created."
    echo "  Try: sudo ./tools/profiling/multi_goose_profile.sh $NUM_GEESE $DURATION"
    exit 1
fi
echo "  Trace captured."

# ── Step 6: Analyze ───────────────────────────────────────────────────────────
echo ""
echo "[6/6] Analyzing trace..."
"$HERE/analyze_trace.sh" "$TRACE_FILE" "$OUTPUT_DIR/hotspots.txt" || true

FINAL_RSS=$(ps -o rss= -p "$CADGOOSE_PID" 2>/dev/null || echo "0")

# ── Results ───────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  RESULTS  (${NUM_GEESE} geese, ${DURATION}s)"
echo "=============================================="
echo "  Output dir:  $OUTPUT_DIR"
echo "  Trace:       $TRACE_FILE"
echo "  Hotspots:    $OUTPUT_DIR/hotspots.txt"
echo "  RSS before:  $((INIT_RSS / 1024)) MB"
echo "  RSS after:   $((FINAL_RSS / 1024)) MB"
echo ""
echo "Open in Instruments:"
echo "  open \"$TRACE_FILE\""
echo ""
echo "Done at: $(date)"
