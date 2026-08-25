#!/bin/bash
# hotspot_profile.sh — One-shot automated CPU hotspot profiling
#
# Workflow:
#   1. Build CadGoose (Release)
#   2. Launch it and wait for MCP socket
#   3. Record 60-second Time Profiler trace
#   4. Analyze trace and print ranked hotspot report
#   5. Kill CadGoose and save everything to /tmp/hotspot_<timestamp>/
#
# Usage:
#   ./tools/profiling/hotspot_profile.sh [DURATION_SECONDS]
#
# Exit codes:
#   0  = success
#   1  = usage error
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
DURATION="${1:-60}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="/tmp/hotspot_${TIMESTAMP}"
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
echo "  CadGoose Hotspot Profile"
echo "  Duration: ${DURATION}s"
echo "  Output:   $OUTPUT_DIR"
echo "  Started:  $(date)"
echo "=============================================="
echo ""

# ── Step 1: Kill any existing CadGoose ────────────────────────────────────────
echo "[1/5] Killing any existing CadGoose..."
pkill -x "CadGoose" 2>/dev/null || true
sleep 1
rm -f "$SOCKET"
echo "  Done."

# ── Step 2: Build ─────────────────────────────────────────────────────────────
echo ""
echo "[2/5] Building CadGoose (Release)..."
cd "$BUILD_DIR"
# Profile the PRODUCTION config: CG_DISABLE_DEBUG_LOG=ON compiles out debug
# logging. Without it, BELog/os_log string-formatting inflates any path that
# triggers drawRect (e.g. window resizes) and misrepresents the real hotspots.
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS_RELEASE="-O2" \
    -DCG_DISABLE_DEBUG_LOG=ON > /dev/null 2>&1 || true
# Generator-agnostic build (this dir is Ninja, not Make — `make CadGoose` was a
# silent no-op that left a stale binary in place).
if ! cmake --build "$BUILD_DIR" --target CadGoose -j"$(sysctl -n hw.logicalcpu)" 2>&1 | tail -5; then
    echo "  FAIL: Build failed."
    exit 97
fi
echo "  Build OK."

# ── Step 3: Launch CadGoose ───────────────────────────────────────────────────
echo ""
echo "[3/5] Launching CadGoose..."
# NOTE: must use --foreground. Without it the binary daemonizes (posix_spawn a
# child, parent exits), so $! is the short-lived launcher PID, not the real
# process — xctrace would attach to nothing and the PID check below false-fails.
"$BUILD_DIR/CadGoose" --foreground > "$OUTPUT_DIR/cadgoose.log" 2>&1 &
CADGOOSE_PID=$!
echo "  PID: $CADGOOSE_PID"

# Wait for MCP socket (up to 30s)
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

# Let the app settle
echo "  Settling for 3s..."
sleep 3

# ── Step 4: Record Time Profiler trace ────────────────────────────────────────
echo ""
echo "[4/5] Recording ${DURATION}s Time Profiler trace..."
echo "  Trace: $TRACE_FILE"

xctrace record \
    --template "Time Profiler" \
    --duration "$DURATION" \
    --pid "$CADGOOSE_PID" \
    --output "$TRACE_FILE" \
    2>&1 | tail -5

if [ ! -e "$TRACE_FILE" ]; then
    echo "  FAIL: Trace file not created."
    echo "  (xctrace requires sudo or profiling entitlement on some macOS versions)"
    echo "  Try: sudo ./tools/profiling/hotspot_profile.sh"
    exit 1
fi
echo "  Trace captured: $TRACE_FILE"

# ── Step 5: Analyze ───────────────────────────────────────────────────────────
echo ""
echo "[5/5] Analyzing trace..."
"$HERE/analyze_trace.sh" "$TRACE_FILE" "$OUTPUT_DIR/hotspots.txt" || true

# ── Results ───────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  RESULTS"
echo "=============================================="
echo "  Output dir: $OUTPUT_DIR"
echo "  Trace:      $TRACE_FILE"
echo "  Hotspots:   $OUTPUT_DIR/hotspots.txt"
echo "  CadGoose:   $OUTPUT_DIR/cadgoose.log"
echo ""
echo "Open in Instruments:"
echo "  open \"$TRACE_FILE\""
echo ""
echo "Peak RSS during run:"
ps -o rss= -p "$CADGOOSE_PID" 2>/dev/null | awk '{printf "  %d MB\n", $1/1024}' || echo "  (process ended)"
echo ""
echo "Done at: $(date)"
