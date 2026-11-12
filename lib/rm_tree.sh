# shellcheck shell=bash
# Sourced. goh_rm_tree <path>...: remove a tree a CONSUMER's code wrote, whatever modes it left.
#
# `rm -rf` cannot remove a file under a directory without `u+w`, and a test that checks "the export is
# read-only" leaves exactly that in its tmp_path. ZeroThunder's push gate (2026-11-12) passed every
# check, then ended red with no `✗` line: `rm -rf "$PYTEST_DEBUG_TEMPROOT"` printed 1284 "Permission
# denied" lines and, under `set -e`, the failed rm was the gate's exit. pytest's own cleanup copes with
# a read-only tree; this does too: make it writable (and clear macOS user-immutable flags), then remove.
#
# A residue that STILL cannot be removed is a warning naming the path, never a failure and never
# silent: the verdict was already given by the checks, and a leftover temp directory is the machine's
# housekeeping, not a defect in the code under test. Returns 0 either way. The caller's `set -e` must
# not be able to end the gate over cleanup.
goh_rm_tree() {
    local p
    for p in "$@"; do
        [ -n "$p" ] || continue
        { [ -e "$p" ] || [ -L "$p" ]; } || continue
        if [ ! -L "$p" ]; then
            chflags -R nouchg,noschg "$p" 2>/dev/null || true
            chmod -R u+rwx "$p" 2>/dev/null || true
        fi
        rm -rf "$p" 2>/dev/null || true
        if [ -e "$p" ] || [ -L "$p" ]; then
            if declare -F warn >/dev/null 2>&1; then
                warn "could not remove $p (left in place; a read-only residue is not a failed gate)"
            else
                printf '⚠ could not remove %s (left in place)\n' "$p" >&2
            fi
        fi
    done
    return 0
}
