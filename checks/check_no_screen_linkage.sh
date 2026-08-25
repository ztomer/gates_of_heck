#!/usr/bin/env bash
# check_no_screen_linkage.sh — DYNAMIC half of the screen-presentation gate.
#
# The static checker (checks/check_no_screen_presentation.py) reasons about
# what test SOURCES ask for. This one inspects what a BUILT test binary can
# DO: a process cannot move or capture the pointer or grab the screen without
# calling one of a short list of functions, so if the link table imports none
# of them, it cannot — whatever any runtime measurement shows (a shared Mac's
# moving cursor proves nothing about whose it is). Ported from necrohand
# tools/check_headless_tests.py audit_bundle(); split out because it needs a
# BUILD, which no static grep may require.
#
#   check_no_screen_linkage.sh <built-test-binary> [more binaries...]
#
# Nothing is rebuilt here: point it at existing .xctest/Contents/MacOS/*
# binaries (or .o/.dylib objects) after the suite builds.
#
# Exit codes: 0 clean · 1 forbidden symbol linked · 2 usage/config error.
set -euo pipefail

command -v nm >/dev/null 2>&1 || {
    echo "✗ [no_linkage] 'nm' not found on PATH — cannot audit link tables" >&2
    exit 2
}
[ $# -ge 1 ] || {
    echo "usage: check_no_screen_linkage.sh <built-test-binary> [more...]" >&2
    exit 2
}

# A process cannot touch the real cursor/screen/input without one of these.
# Each entry says WHAT it would do, so a finding names the capability.
FORBIDDEN="CGWarpMouseCursorPosition
CGDisplayMoveCursorToPoint
CGEventPost
CGEventTapCreate
CGAssociateMouseAndMouseCursorPosition
CGDisplayHideCursor
CGDisplayShowCursor
IOHIDPostEvent
SCStreamStart
SCScreenshotManager
CGWindowListCreateImage"

fail=0
for bin in "$@"; do
    if [ ! -f "$bin" ]; then
        echo "✗ [no_linkage] binary not found: $bin" >&2
        fail=2
        continue
    fi
    table=$(nm -u "$bin" 2>/dev/null) || {
        echo "✗ [no_linkage] nm -u failed on $bin (not an object/binary?)" >&2
        fail=2
        continue
    }
    found=""
    while IFS= read -r sym; do
        [ -n "$sym" ] || continue
        # macOS links these as _<symbol>; substring match covers both spellings.
        if printf '%s\n' "$table" | grep -q "$sym"; then
            found="$found$sym"$'\n'
        fi
    done <<<"$FORBIDDEN"
    if [ -n "$found" ]; then
        echo "✗ [no_linkage] $bin links screen/input-grabbing symbols:" >&2
        printf '%s' "$found" | sort -u | sed 's/^/    /' >&2
        fail=1
    else
        echo "→ [no_linkage] OK — $bin imports none of the forbidden symbols"
    fi
done

if [ "$fail" -eq 1 ]; then
    echo "  Tests must drive an EMULATED pointer and offscreen renders; the" >&2
    echo "  real cursor, screen and window server belong to the user." >&2
fi
exit "$((fail > 0 ? (fail == 2 ? 2 : 1) : 0))"
