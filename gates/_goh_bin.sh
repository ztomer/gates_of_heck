# shellcheck shell=bash
# _goh_bin.sh — sourced, never run. The ONE resolution of the native `goh` binary.
#
# It was written three times (structural.sh, and twice in rust_gate.sh for lints and no-allow),
# and the copies had drifted by the time they were compared (2026-09-23): the lints copy, given a
# GOH_BIN that was set but not executable, fell through to bin/goh — a different binary than the
# one named — where the other two reported it and ran Python. Every caller now asks here.
#
# Order: GOH_NO_NATIVE forces Python; an explicit GOH_BIN is trusted or REPORTED, never silently
# replaced; then the copy install.sh publishes at bin/goh; then `goh` on PATH.
#
# Sets `goh_native` (the binary, or empty) and `goh_native_why` (why it is empty, for the one line
# the caller prints; empty when GOH_NO_NATIVE asked for Python on purpose).

goh_resolve_native() {
    goh_native=""
    goh_native_why=""
    [ -n "${GOH_NO_NATIVE:-}" ] && return 0
    if [ -n "${GOH_BIN:-}" ]; then
        if [ -x "${GOH_BIN}" ]; then
            goh_native="$GOH_BIN"
        else
            goh_native_why="GOH_BIN=$GOH_BIN is not an executable"
        fi
        return 0
    fi
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -x "$here/../bin/goh" ]; then
        goh_native="$here/../bin/goh"
    elif command -v goh >/dev/null 2>&1; then
        goh_native="$(command -v goh)"
    else
        goh_native_why="goh binary not built (cd $here/.. && ./install.sh builds it)"
    fi
}
