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
    local here want
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    want="$(goh_head_stamp "$here/..")"
    # bin/goh is OURS to rebuild: built from other source than HEAD's (or before stamps existed),
    # it is rebuilt from HEAD here, under build-goh.sh's lock, and then used. A version check alone
    # passed a same-version binary that lacked a step (2026-10-05, BACKLOG C3).
    if [ -n "$want" ] && [ "$(goh_stamp_of "$here/../bin/goh")" != "$want" ]; then
        echo "· bin/goh was not built from HEAD's source -- rebuilding it (scripts/build-goh.sh)" >&2
        bash "$here/../scripts/build-goh.sh" --if-stale >&2 \
            || echo "✗ the rebuild failed (above); this run uses the Python checkers" >&2
    fi
    if [ -x "$here/../bin/goh" ] && { [ -z "$want" ] || [ "$(goh_stamp_of "$here/../bin/goh")" = "$want" ]; }; then
        goh_native="$here/../bin/goh"
    elif command -v goh >/dev/null 2>&1 && [ "$(goh_stamp_of "$(command -v goh)")" = "$want" ]; then
        goh_native="$(command -v goh)"
    elif [ -n "$want" ] && [ -x "$here/../bin/goh" ]; then
        goh_native_why="bin/goh is not built from HEAD's source and could not be rebuilt"
    else
        goh_native_why="goh binary not built (cd $here/.. && ./install.sh builds it)"
    fi
}

# goh_head_stamp <goh root> -- the git trees of the binary's build inputs at HEAD, as
# crates/goh/build.rs stamps them; empty when the root is not a git checkout (nothing to compare).
goh_head_stamp() {
    # Captured FIRST, then joined: a revision git cannot resolve (no HEAD yet, a missing input) is
    # ECHOED to stdout, and under pipefail a failing git inside a pipe killed the gate silently.
    local out
    # shellcheck disable=SC2046  # word-splitting git's variable list is the point
    out="$(unset $(git rev-parse --local-env-vars 2>/dev/null)
           git -C "$1" rev-parse HEAD:crates HEAD:Cargo.toml HEAD:Cargo.lock HEAD:rust-toolchain.toml \
               2>/dev/null)" || return 0
    printf '%s' "$out" | tr '\n' ' ' | sed 's/ $//'
}

# goh_stamp_of <binary> -- what it says it was built from; empty for a pre-stamp build.
goh_stamp_of() {
    [ -x "$1" ] || return 0
    "$1" source-tree 2>/dev/null | head -n1 || true
}
