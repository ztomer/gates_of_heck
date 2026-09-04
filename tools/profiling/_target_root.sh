# _target_root.sh — resolve WHICH repo a centralized profiling harness acts on.
# (sourced, never executed: no shebang by design; dialect declared below).
# shellcheck shell=bash
#
# These scripts used to live at <repo>/tools/profiling/ and derived the target
# project two levels up from their own location. Canonized here, the location
# no longer encodes the target, so resolution is explicit:
#
#   1. GOH_PROFILE_TARGET   env override (absolute repo root)
#   2. the invocation CWD   (the per-repo shims exec us from the repo root)
#
# Sourced by every script in tools/profiling. No side effects beyond defining
# the two functions; scripts call `_goh_profile_target` where they used to
# compute PROJ/ROOT/PROJECT_DIR from dirname "$0".

_goh_profile_target() {
    if [ -n "${GOH_PROFILE_TARGET:-}" ]; then
        printf '%s\n' "$GOH_PROFILE_TARGET"
        return 0
    fi
    pwd
}

# The script's own canonical home (this directory), for reaching sibling
# scripts without assuming the TARGET still carries a full shim set.
_goh_profile_here() {
    cd "$(dirname "${BASH_SOURCE[1]}")" && pwd
}
