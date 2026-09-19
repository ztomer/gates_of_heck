#!/usr/bin/env bash
# swift_toolchain.sh — resolve THE `swift` a house script builds with, once.
#
# Sourced, never executed. `goh_swift_resolve` sets:
#   SWIFT         absolute path of the swift driver to invoke
#   SWIFT_ORIGIN  override | xcrun | path   (printed by callers, so the log
#                 names the toolchain a build ran on)
#
# WHY PATH IS NOT THE ANSWER. A swiftly toolchain on PATH shadows Xcode's:
# on 2026-09-16 `swift build` under swiftly 6.3.3 rejected the
# `-target-arch-variant` flag Xcode 6.4's driver emits and could not find
# `SwiftData` (no platform SDK), so ZincTender's pre-push gate and its
# packaging script were "environmentally broken" for three days and a
# corrected rules table never shipped — while `/usr/bin/swift build` on the
# same tree took 14 s. The platform SDK lives with Xcode's toolchain, and
# `xcrun --find swift` names that one regardless of PATH order.
#
# Resolution, first hit wins:
#   1. GOH_SWIFT=/path/to/swift   explicit; refused when not executable
#   2. xcrun --find swift         Xcode's toolchain (macOS)
#   3. command -v swift           anything else (Linux, no Xcode)
# A failure names every rung it tried.
#
#   . "$GOH_DIR/gates/swift_toolchain.sh"; goh_swift_resolve
#   "$SWIFT" build -c release …

_goh_swift_fail() {
    if declare -F die >/dev/null 2>&1; then die "$1"; fi
    printf '✗ %s\n' "$1" >&2
    exit 1
}

goh_swift_resolve() {
    local found
    if [ -n "${GOH_SWIFT:-}" ]; then
        [ -x "$GOH_SWIFT" ] || _goh_swift_fail "GOH_SWIFT names no executable: $GOH_SWIFT"
        SWIFT="$GOH_SWIFT"; SWIFT_ORIGIN="override"
        return 0
    fi
    if command -v xcrun >/dev/null 2>&1; then
        if found="$(xcrun --find swift 2>/dev/null)" && [ -n "$found" ] && [ -x "$found" ]; then
            SWIFT="$found"; SWIFT_ORIGIN="xcrun"
            return 0
        fi
    fi
    found="$(command -v swift 2>/dev/null || true)"
    if [ -n "$found" ]; then
        SWIFT="$found"; SWIFT_ORIGIN="path"
        return 0
    fi
    _goh_swift_fail "swift: not found — GOH_SWIFT unset, \`xcrun --find swift\` answered nothing, none on PATH"
}
