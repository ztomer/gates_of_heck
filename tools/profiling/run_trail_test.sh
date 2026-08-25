#!/bin/bash
# run_trail_test.sh — Fully automated trail detection test runner
#
# Orchestrates: permissions check → window isolation → build → launch → test → restore
# Run from any terminal; auto-re-launches in Ghostty (which has Screen Recording).
#
# Outcomes:
#   Exit 0  = clean pass
#   Exit 10 = trail detected
#   Exit 11 = item not visible (sub-layer regression)
#   Exit 97 = build failure
#   Exit 98 = socket timeout
#   Exit 99 = Screen Recording permission missing

set -euo pipefail

# Target repo: explicit GOH_PROFILE_TARGET wins, else the invocation CWD
# (the per-repo shims exec us from the repo root). Location no longer encodes
# the target now that this harness is canonized in gates_of_heck.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_target_root.sh"
PROJ="$(_goh_profile_target)"
BUILD_DIR="$PROJ/build"
SOCKET="/tmp/desktop-goose-mcp.sock"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="/tmp/trail_test_${TIMESTAMP}"
LOCKFILE="/tmp/.cadgoose_trail_test.lock"
RETRYFILE="/tmp/.cadgoose_trail_test_retried"

cleanup() { rm -f "$LOCKFILE"; }
trap cleanup EXIT

# ── Infinite-loop guard ──────────────────────────────────────────────
if [ -f "$LOCKFILE" ]; then
    OTHER_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if kill -0 "$OTHER_PID" 2>/dev/null; then
        echo "ERROR: Another trail test instance (PID $OTHER_PID) is already running."
        exit 1
    fi
    echo "WARNING: Stale lock from PID $OTHER_PID. Removing."
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"

# ── Detect screen recording / re-launch in Ghostty ────────────────
IS_GHOSTTY=0
if echo "${TERM_PROGRAM:-}" | grep -qi ghostty; then
    IS_GHOSTTY=1
fi

# Retry file exists and we're not in Ghostty → second attempt failed
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

# Not in Ghostty → launch into it
if [ "$IS_GHOSTTY" -eq 0 ]; then
    touch "$RETRYFILE"
    echo "Launching in Ghostty (needs Screen Recording permission)..."
    open -na Ghostty --args -e "$PROJ/tools/profiling/run_trail_test.sh"
    exit 0
fi

# In Ghostty — clean up and proceed
rm -f "$RETRYFILE"

mkdir -p "$OUTPUT_DIR"
# Tee stdout/stderr to both terminal and log file
exec > >(tee "$OUTPUT_DIR/log.txt") 2>&1

echo "=============================================="
echo "  CadGoose Trail Detection Test (v4)"
echo "  Started: $(date)"
echo "=============================================="
echo ""
echo "Output directory: $OUTPUT_DIR"
echo ""

# ── Step 1: Verify Screen Recording (via SCStream probe) ───────────
echo "[1/7] Checking Screen Recording permission..."
# Quick probe: compile and run a minimal SCStream test
PROBE_FILE="/tmp/sc_probe_${TIMESTAMP}.m"
PROBE_BIN="/tmp/sc_probe_${TIMESTAMP}"
cat > "$PROBE_FILE" << 'OBJC'
#import <Foundation/Foundation.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
int main() {
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    __block BOOL ok = NO;
    [SCShareableContent getShareableContentWithCompletionHandler:
        ^(SCShareableContent* _Nullable sc, NSError* _Nullable err) {
            ok = (sc != nil && sc.displays.count > 0);
            if (err) {
                fprintf(stderr, "SC error: %s\n", err.localizedDescription.UTF8String);
            }
            dispatch_semaphore_signal(sem);
        }];
    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));
    return ok ? 0 : 1;
}
OBJC
clang -framework Foundation -framework ScreenCaptureKit -o "$PROBE_BIN" "$PROBE_FILE" 2>/dev/null
SC_OK=0
if [ -x "$PROBE_BIN" ]; then
    "$PROBE_BIN" && SC_OK=1 || true
fi
rm -f "$PROBE_FILE" "$PROBE_BIN"

if [ "$SC_OK" -ne 1 ]; then
    echo "  FAIL: Screen Recording permission not granted to Ghostty."
    echo "  Go to System Settings → Privacy & Security → Screen Recording"
    echo "  and ensure 'Ghostty' is enabled."
    echo ""
    echo "  Then re-run this script."
    exit 99
fi
echo "  OK — Screen Recording available."

# ── Step 2: Save & hide all visible apps ────────────────────────────
echo ""
echo "[2/7] Hiding desktop windows for clean capture..."

HIDDEN_APPS_FILE="$OUTPUT_DIR/hidden_apps.txt"
osascript -e '
tell application "System Events"
    set appList to {}
    set procs to every process whose visible is true and background only is false
    repeat with p in procs
        set pname to name of p
        if pname is not "Ghostty" and pname is not "Finder" then
            set end of appList to pname
        end if
    end repeat
    return appList
end tell
' | tr ',' '\n' | sed 's/^ *//' > "$HIDDEN_APPS_FILE"

HIDDEN_COUNT=$(wc -l < "$HIDDEN_APPS_FILE" | tr -d ' ')
echo "  Found $HIDDEN_COUNT apps to hide (Ghostty + Finder excluded)."

# Hide them
osascript -e '
tell application "System Events"
    set procs to every process whose visible is true and background only is false
    repeat with p in procs
        set pname to name of p
        if pname is not "Ghostty" and pname is not "Finder" then
            set visible of p to false
        end if
    end repeat
end tell
' 2>/dev/null || true

sleep 1
echo "  Windows hidden."

# ── Step 3: Build ───────────────────────────────────────────────────
echo ""
echo "[3/7] Building..."
cd "$BUILD_DIR"
cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -3
make -j$(sysctl -n hw.logicalcpu) CadGoose trail_detection_test 2>&1 | tail -5
BUILD_OK=$?
if [ $BUILD_OK -ne 0 ]; then
    echo "  BUILD FAILED."
    exit 97
fi
echo "  Build OK."

# ── Step 4: Kill old CadGoose, launch fresh ─────────────────────────
echo ""
echo "[4/7] Launching fresh CadGoose..."
pkill -x "CadGoose" 2>/dev/null || true
sleep 2

"$BUILD_DIR/CadGoose" >"$OUTPUT_DIR/cadgoose.log" 2>&1 &
CADGOOSE_PID=$!
echo "  PID: $CADGOOSE_PID"

# ── Step 5: Wait for MCP socket ─────────────────────────────────────
echo ""
echo "[5/7] Waiting for MCP socket..."
SOCKET_OK=0
for i in $(seq 1 30); do
    if [ -S "$SOCKET" ]; then
        SOCKET_OK=1
        echo "  Socket ready after ${i}s"
        break
    fi
    # Check process is still alive
    kill -0 "$CADGOOSE_PID" 2>/dev/null || {
        echo "  CadGoose died during startup."
        break
    }
    sleep 1
done

if [ "$SOCKET_OK" -ne 1 ]; then
    echo "  FAIL: Socket not found after 30s"
    kill "$CADGOOSE_PID" 2>/dev/null || true
    exit 98
fi

# Brief settle for goose to spawn
sleep 3

# ── Step 6: Run trail detection test ────────────────────────────────
echo ""
echo "[6/7] Running trail detection test..."
echo "  (cyan test image, SCStream at vsync rate)"
echo ""

# The test binary is in the non-release build dir
"$BUILD_DIR/trail_detection_test" 2>&1
EXIT_CODE=$?
echo ""
echo "  Test exit code: $EXIT_CODE"

# ── Step 7: Restore hidden apps ─────────────────────────────────────
echo ""
echo "[7/7] Restoring hidden apps..."

while IFS= read -r appname; do
    [ -z "$appname" ] && continue
    osascript -e "tell application \"$appname\" to set visible to true" 2>/dev/null || true
done < "$HIDDEN_APPS_FILE"

echo "  Apps restored."

# ── Results ─────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  RESULTS: $(date)"
echo "=============================================="
case $EXIT_CODE in
    0)
        echo "  ✓ CLEAN — No trail detected."
        echo "  Held item CALayer renders correctly, no artifacts."
        ;;
    10)
        echo "  ✗ TRAIL DETECTED."
        echo "  Inspect captured frames: /tmp/trail_test_frames/"
        echo "  Full log: $OUTPUT_DIR/log.txt"
        ;;
    11)
        echo "  ✗ ITEM NOT VISIBLE during carry."
        echo "  The held item sublayer may not be rendering."
        echo "  Inspect captured frames: /tmp/trail_test_frames/"
        echo "  Full log: $OUTPUT_DIR/log.txt"
        ;;
    *)
        echo "  ⚠  Test error (code $EXIT_CODE)."
        echo "  Full log: $OUTPUT_DIR/log.txt"
        ;;
esac
echo ""

# Copy test frames to our output dir for easy reference
if [ -d /tmp/trail_test_frames ]; then
    cp -r /tmp/trail_test_frames "$OUTPUT_DIR/frames" 2>/dev/null || true
    echo "Frames copied to: $OUTPUT_DIR/frames/"
fi

echo "Log saved to: $OUTPUT_DIR/log.txt"
echo ""
echo "CadGoose PID $CADGOOSE_PID still running."
echo "Type 'exit' or press Ctrl+D to close."
echo ""

exec $SHELL
