#!/usr/bin/env bash
# tree_lock.sh — one build tree, one gate at a time.
#
# HOUSE LIB. A cold gate wipes `.build` (or DerivedData) and rebuilds it. Two of
# them on one checkout at once is not "slow", it is RED: the second's wipe lands
# while the first is in its test phase, and every test bundle the first goes to
# dlopen is gone -- "Failed to open test bundle ... (no such file)" x4, signal 5,
# push refused, and the failure names the tests, never the peer. Measured
# 2026-09-21 on ZoneWM: two pushes twelve minutes apart from one session, the
# first died in `test` the moment the second announced "cold build requested".
#
# So a gate that touches the tree takes THIS lock for its whole run, and a peer
# WAITS -- serialising is the only outcome that leaves both green.
#
# WHY flock(2) AND NOT mkdir, unlike desktop_lock.sh. That lock excludes peers
# across every checkout on the machine and must survive owners that die in every
# possible way, so it carries liveness, start-time and max-hold logic. This lock
# is per-checkout and its owner is a bash process: a kernel flock on an fd the
# shell holds open is released on ANY exit, including SIGKILL, so there is no
# stale state to reason about at all. macOS ships no flock(1); python3 takes the
# lock on the shell's own fd 9 (the lock lives on the open file description,
# which the child inherits and shares), then exits -- the shell keeps fd 9 and
# with it the lock. Proven 2026-09-21: a waiter blocks behind a holder and is
# released the instant the holder is `kill -9`ed.
#
# Requires tui/lib.sh (info/warn/step/ok) to be sourced by the caller.
#
# Usage:
#     . "$GOH_DIR/lib/tree_lock.sh"
#     tree_lock_acquire "$repo" "swift gate"     # blocks; announces the wait
#
# The lock file is `<repo>/.goh-tree.lock` (add to the repo's global gitignore
# pattern or .git/info/exclude; it is empty and never removed on purpose --
# removing a flock file under a holder hands the next caller a fresh, unlocked
# inode, which is the race this file exists to prevent).

TREE_LOCK_FD=9

# A peer's pid is written into the file for the wait message only; the lock
# itself is the flock, never the content.
tree_lock_acquire() {
    local repo="${1:?repo}" label="${2:-gate}" lock
    lock="$repo/.goh-tree.lock"
    # Bash cannot `exec {var}>` into a variable fd portably across 3.2 (macOS's
    # /bin/bash); fd 9 is fixed and documented above.
    exec 9>>"$lock"
    if ! python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null; then
        local holder=""
        # Pure bash on purpose: sed/head/tr are rewritten under some sessions
        # (desktop_lock.sh has the story) and a lock message is not worth a
        # dependency on which one this shell got.
        IFS= read -r holder < "$lock" 2>/dev/null || true
        info "the build tree is held by ${holder:-another gate}"
        step "waiting for it rather than wiping .build under its tests"
        python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX)'
        ok "build tree acquired"
    fi
    # Truncate-and-write our own identity for the next waiter's message. The
    # fd is O_APPEND (>>) so the open never truncated a holder's record while
    # we were still a waiter; now that we hold the lock, we own the content.
    : > "$lock"
    printf '%s (pid %s, %s)' "$label" "$$" "$(date '+%H:%M:%S')" >&9
}
