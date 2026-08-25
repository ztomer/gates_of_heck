#!/bin/bash
# run_multi_goose_test.sh
# Multi-goose regression test — structural verification via command socket.
# Does NOT need Screen Recording permission (unlike SCStream-based tests).
#
# Exit codes:
#   0  = all 3 geese functional
#   1  = connection / socket error
#   11+ = one or more geese failed

set -e

cd "$(dirname "$0")/../.."
BUILD_DIR="$PWD/build"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTDIR="/tmp/multi_goose_test_${TIMESTAMP}"
mkdir -p "$OUTDIR"

exec > "$OUTDIR/log.txt" 2>&1

echo "=============================================="
echo "  Multi-Goose Regression Test"
echo "  Started: $(date)"
echo "=============================================="
echo ""

# Build
echo "[1/4] Building..."
make -j$(sysctl -n hw.logicalcpu) -C "$BUILD_DIR" multi_goose_test 2>&1 | tail -3
echo "  Build OK."
echo ""

# Kill stale CadGoose, clean sockets
echo "[2/4] Launching CadGoose..."
pkill -9 CadGoose 2>/dev/null || true
sleep 1
rm -f /tmp/desktop-goose.sock /tmp/desktop-goose-mcp.sock
sleep 1

"$BUILD_DIR/CadGoose" &
CADGOOSE_PID=$!
echo "  PID: $CADGOOSE_PID"

for i in $(seq 1 30); do
    if [ -S /tmp/desktop-goose.sock ]; then
        echo "  Socket ready after ${i}s"
        break
    fi
    sleep 1
done
if [ ! -S /tmp/desktop-goose.sock ]; then
    echo "  ERROR: Socket not ready after 30s"
    exit 98
fi
echo ""

# Run test
echo "[3/4] Running multi-goose test..."
"$BUILD_DIR/multi_goose_test"
EXIT_CODE=$?
echo ""

# Cleanup
echo "[4/4] Cleaning up..."
kill "$CADGOOSE_PID" 2>/dev/null || true

echo ""
echo "=============================================="
echo "  RESULTS: $(date)"
echo "=============================================="
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "  ✓ All 3 geese functional."
else
    echo "  ✗ Exit code $EXIT_CODE"
fi
echo ""
echo "Log saved to: $OUTDIR/log.txt"
echo ""

exit "$EXIT_CODE"
