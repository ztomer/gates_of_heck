#!/bin/bash
# run_soak_test.sh — Fetch visibility soak test
#
# Repeatedly forces fetch cycles (cyan test image) and verifies the
# held item sublayer is visible via SCStream capture. Runs until
# failure detected or 10 minutes pass.
#
# Automatically re-launches in Ghostty if Screen Recording permission
# is not available in the current terminal (uses lock file to prevent
# infinite re-launch loops).
#
# Exit codes:
#   0   All cycles passed (item visible every time)
#   11  Failure detected (item not visible during a carry phase)
#   97  Build failure
#   98  Socket timeout (CadGoose didn't start)
#   99  Screen Recording permission missing
#
# Usage:
#   ./tools/profiling/run_soak_test.sh

set -euo pipefail

# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
ROOT="$(_goh_profile_target)"
BUILD_DIR="$ROOT/build"
LOCKFILE="/tmp/.cadgoose_soak_test.lock"
RETRYFILE="/tmp/.cadgoose_soak_test_retried"

cleanup() {
    rm -f "$LOCKFILE"
}
trap cleanup EXIT

# ---- Infinite-loop guard ----
# If lock exists, another instance is already running
if [ -f "$LOCKFILE" ]; then
    OTHER_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if kill -0 "$OTHER_PID" 2>/dev/null; then
        echo "ERROR: Another soak test instance (PID $OTHER_PID) is already running."
        exit 1
    fi
    echo "WARNING: Stale lock from PID $OTHER_PID. Removing."
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"

# ---- Detect screen recording ----
IS_GHOSTTY=0
if echo "${TERM_PROGRAM:-}" | grep -qi ghostty; then
    IS_GHOSTTY=1
fi

# If we're NOT in Ghostty but the retry file exists, a previous attempt
# failed — Screen Recording permission is missing.
if [ "$IS_GHOSTTY" -eq 0 ] && [ -f "$RETRYFILE" ]; then
    echo ""
    echo "================================================================"
    echo "  Screen Recording permission required but not available."
    echo ""
    echo "  1. Open System Settings → Privacy & Security → Screen Recording"
    echo "  2. Ensure Ghostty is in the list and CHECKED"
    echo "  3. Re-run this script from Ghostty"
    echo "================================================================"
    rm -f "$RETRYFILE"
    exit 99
fi

# Not in Ghostty and no retry file → launch into Ghostty
if [ "$IS_GHOSTTY" -eq 0 ]; then
    touch "$RETRYFILE"
    echo "Launching in Ghostty (needs Screen Recording permission)..."
    open -na Ghostty --args -e "$ROOT/tools/profiling/run_soak_test.sh"
    exit 0
fi

# We're in Ghostty — clean up retry marker and proceed
rm -f "$RETRYFILE"

LOG_DIR="/tmp/soak_test_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_DIR/run.log") 2>&1

echo "======================================"
echo "  Soak Fetch Visibility Test"
echo "======================================"
echo "  Build dir: $BUILD_DIR"
echo "  Log dir:   $LOG_DIR"
echo "======================================"

# ---- Step 0: Kill existing CadGoose ----
echo ""
echo "[0/5] Killing existing CadGoose processes..."
pkill -9 CadGoose 2>/dev/null || true
sleep 1
if pgrep -x CadGoose >/dev/null 2>&1; then
    echo "  WARNING: CadGoose still running, trying harder..."
    pkill -9 -x CadGoose 2>/dev/null || true
    sleep 2
fi
rm -f /tmp/desktop-goose.sock
echo "  Done."

# ---- Step 1: Build ----
echo ""
echo "[1/5] Building soak_fetch_test..."
cd "$BUILD_DIR"
cmake .. -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1
make -j$(sysctl -n hw.logicalcpu) soak_fetch_test 2>&1 | tail -5
if [ $? -ne 0 ]; then
    echo "  FAIL: Build failed."
    exit 97
fi
echo "  Build OK."

# ---- Step 2: Launch CadGoose ----
echo ""
echo "[2/5] Launching CadGoose..."
"$BUILD_DIR/CadGoose" --debug &
CADGOOSE_PID=$!
echo "  PID: $CADGOOSE_PID"

# ---- Step 3: Wait for socket ----
echo ""
echo "[3/5] Waiting for command socket..."
SOCKET="/tmp/desktop-goose.sock"
for i in $(seq 1 60); do
    if [ -S "$SOCKET" ]; then
        echo "  Socket ready after ${i}s."
        break
    fi
    if ! kill -0 $CADGOOSE_PID 2>/dev/null; then
        echo "  FAIL: CadGoose died during startup."
        exit 98
    fi
    sleep 1
done
if [ ! -S "$SOCKET" ]; then
    echo "  FAIL: Socket not available after 60s."
    exit 98
fi
sleep 2

# ---- Step 4: Run soak test ----
echo ""
echo "[4/5] Running soak_fetch_test..."
echo "  (up to 10 minutes — will auto-exit on failure or completion)"
echo ""
"$BUILD_DIR/soak_fetch_test" 2>&1 | tee "$LOG_DIR/test_output.txt"
TEST_EXIT=${PIPESTATUS[0]}
echo ""
echo "  Test exit code: $TEST_EXIT"

# ---- Step 5: Report ----
echo ""
echo "[5/5] Done."
echo "  Log: $LOG_DIR/run.log"
echo "  Test output: $LOG_DIR/test_output.txt"

if [ -d "/tmp/soak_fetch_test" ]; then
    echo "  Frames: /tmp/soak_fetch_test/"
fi

case $TEST_EXIT in
    0)  echo "  RESULT: All cycles passed ✓" ;;
    11) echo "  RESULT: FAILURE — item not visible during carry" ;;
    1)  echo "  RESULT: Connection error" ;;
    2)  echo "  RESULT: SCStream permission error" ;;
    *)  echo "  RESULT: Unknown exit code $TEST_EXIT" ;;
esac

exit $TEST_EXIT
