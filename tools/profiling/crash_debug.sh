#!/bin/bash
# crash_debug.sh — Launch CadGoose under lldb to capture crash backtrace
#
# Usage:
#   ./crash_debug.sh                 # build + launch under lldb
#   ./crash_debug.sh --attach <PID>  # attach to running process
#
# On crash: backtrace saved to /tmp/cadgoose_crash_*.txt

set -euo pipefail

# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
PROJECT_DIR="$(_goh_profile_target)"
BUILD_DIR="$PROJECT_DIR/build"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_FILE="/tmp/cadgoose_crash_${TIMESTAMP}.txt"

echo "=== CadGoose Crash Debug ==="
echo "Timestamp: $TIMESTAMP"
echo "Output: $OUTPUT_FILE"

if [ "${1:-}" = "--attach" ] && [ -n "${2:-}" ]; then
    PID="$2"
    echo "Attaching to PID $PID..."
    lldb -b -p "$PID" -o "bt all" -o "frame variable" -o "quit" 2>&1 | tee "$OUTPUT_FILE"
elif [ ! -f "$BUILD_DIR/CadGoose" ]; then
    echo "Building CadGoose..."
    (cd "$BUILD_DIR" && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(sysctl -n hw.logicalcpu) CadGoose)
    echo "Launching CadGoose under lldb..."
    echo "Trigger the crash by interacting with the app."
    lldb -b "$BUILD_DIR/CadGoose" -o "run" -o "bt all" -o "frame variable" -o "quit" 2>&1 | tee "$OUTPUT_FILE"
else
    echo "Launching CadGoose under lldb..."
    echo "Trigger the crash by interacting with the app."
    lldb -b "$BUILD_DIR/CadGoose" -o "run" -o "bt all" -o "frame variable" -o "quit" 2>&1 | tee "$OUTPUT_FILE"
fi

echo ""
echo "Debug log saved to: $OUTPUT_FILE"
if grep -q "stop reason" "$OUTPUT_FILE" 2>/dev/null; then
    echo "CRASH DETECTED — backtrace in $OUTPUT_FILE"
    exit 10
else
    echo "Process exited normally."
    exit 0
fi
