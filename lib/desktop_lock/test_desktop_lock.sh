#!/usr/bin/env bash
# test_desktop_lock.sh — prove the desktop lock excludes, and that it CANNOT
# deadlock. Every release path is exercised by really killing or really stalling
# a holder, never by simulating one, because the whole value of this lock is what
# it does when a peer dies badly.
#
# NOTE: this suite takes the REAL machine-wide lock at $DESKTOP_LOCK_DIR. If an
# unrelated capture run is mid-flight on this Mac, wait for it rather than
# debugging phantom failures.
set -uo pipefail
GOH_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "${GOH_DIR:-$GOH_ROOT}/tui/lib.sh"
cd "$(dirname "$0")"
source desktop_lock.sh

PASS=0
FAIL=0
TMP="$(mktemp -d "${TMPDIR:-/tmp}/desktop-lock-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
check() {
    if [[ "$2" == "$3" ]]; then ok "$1"; PASS=$((PASS + 1))
    else err "$1 -- expected '$3', got '$2'"; FAIL=$((FAIL + 1)); fi
}

# A holder in its own process, so the PID we probe is real.
#
# THE REDIRECTION IS LOAD-BEARING, and this suite already lied once without it:
# a background child inherits the command-substitution pipe, so `$(holder 30)`
# blocks until that pipe closes -- thirty seconds, by which time the "holder" has
# exited and the lock it left behind is merely stale. Every exclusion test then
# passes against a lock nobody is holding. Send the child's output to /dev/null
# so the substitution returns immediately and the holder is genuinely concurrent.
holder() {  # holder <seconds> -> prints its pid
    bash -c "
        source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
        desktop_lock_acquire 'test holder' >/dev/null 2>&1
        sleep $1
    " >/dev/null 2>&1 &
    echo $!
}

# Guard against that class returning: assert the lock is REALLY held by a live
# owner before any test that depends on contention.
assert_held_by() {  # assert_held_by <pid>
    [[ -d "$DESKTOP_LOCK_DIR" ]] || die "setup: holder never took the lock"
    kill -0 "$1" 2>/dev/null || die "setup: holder $1 is not running"
    [[ "$(head -n 1 "$DESKTOP_LOCK_DIR/owner")" == "$1" ]] || \
        die "setup: lock owner is not the holder we started"
}

rm -rf "$DESKTOP_LOCK_DIR"

# Run a command with a WALL CLOCK on it. Without this, a lock that regressed to spinning on a
# directory it cannot clear would hang the suite instead of going red. Returns 124 when it fired.
run_bounded() {  # run_bounded <seconds> <command...>
    local secs="$1"; shift
    "$@" & local p=$! i=0
    while kill -0 "$p" 2>/dev/null && (( i < secs * 10 )); do sleep 0.1; i=$((i + 1)); done
    if kill -0 "$p" 2>/dev/null; then
        kill -9 "$p" 2>/dev/null; wait "$p" 2>/dev/null
        return 124
    fi
    wait "$p"
}

# One taker, in its own process, with the environment the caller sets for it.
take() {  # take <label>   -> prints GOT when it acquired; the exit code is acquire's
    bash -c "
        source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
        desktop_lock_acquire '$1' && echo GOT
    " 2>&1
}

section "desktop lock"

# 0. THE PATH IS THE HOUSE ONE. Every other project on this Mac takes this lock at this path, so a
#    move here does not break one tool -- it silently removes the exclusion between every pair of
#    them. Pinned because it is the one assertion that fails the moment anyone edits a string.
check "the lock lives at the house-wide path" "$DESKTOP_LOCK_DIR" "/tmp/mac-desktop-ui.lock"

# 1. MUTUAL EXCLUSION. The lock must actually be held against a second taker.
PID="$(holder 30)"; sleep 1; assert_held_by "$PID"
bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    DESKTOP_LOCK_TIMEOUT=2 desktop_lock_acquire 'intruder'
" >/dev/null 2>&1
check "a second acquire is refused while a live peer holds it" "$?" "1"

# 1b. ... and the refusal quotes the ceiling the ENVIRONMENT asked for, not the module default.
#     DESKTOP_LOCK_TIMEOUT is read as ${DESKTOP_LOCK_TIMEOUT:-300}; were it ignored the message
#     would say 300 and the case above would still pass with the wrong default in place.
OUT="$(DESKTOP_LOCK_TIMEOUT=2 take 'intruder')"
check "the wait bound comes from the environment, not the default" \
    "$(grep -c 'after 2s' <<<"$OUT")" "1"

# 1c. The same seam for the AGE ceiling. The holder is live and its start time matches, so with
#     any real ceiling this is refused (case 1). At 0 the ceiling is already passed, so the wedge
#     defence fires and takes the lock from a perfectly live holder.
OUT="$(DESKTOP_LOCK_TIMEOUT=2 DESKTOP_LOCK_MAX_HOLD=0 run_bounded 20 take 'age ceiling from env')"
check "the age ceiling comes from the environment too" "$(grep -c GOT <<<"$OUT")" "1"
kill -9 "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
rm -rf "$DESKTOP_LOCK_DIR"

# 2. SIGKILL -- the case a trap cannot cover. Must reclaim, not wait.
kill -9 "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
OUT="$(DESKTOP_LOCK_TIMEOUT=5 bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    DESKTOP_LOCK_TIMEOUT=5 desktop_lock_acquire 'after sigkill' && echo GOT
" 2>&1)"
check "a SIGKILLed owner's lock is reclaimed" "$(grep -c GOT <<<"$OUT")" "1"
rm -rf "$DESKTOP_LOCK_DIR"

# 3. PID REUSE. A live process wearing the dead owner's number must NOT read as
#    the owner -- otherwise the lock wedges permanently. Forge the owner file to
#    this shell's PID with somebody else's start time.
mkdir -p "$DESKTOP_LOCK_DIR"
printf '%s\n%s\n%s\n' "$$" "Thu Jan  1 00:00:00 1970" "recycled pid" \
    > "$DESKTOP_LOCK_DIR/owner"
OUT="$(DESKTOP_LOCK_TIMEOUT=5 bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    DESKTOP_LOCK_TIMEOUT=5 desktop_lock_acquire 'after reuse' && echo GOT
" 2>&1)"
check "a recycled PID does not impersonate the owner" "$(grep -c GOT <<<"$OUT")" "1"
rm -rf "$DESKTOP_LOCK_DIR"

# 3b. A TRUNCATED owner record is not a stale one. This shell is alive and owns the number in
#     field 1; the start-time and label lines were never written. Reading that as stale would
#     delete a lock a live peer is sitting in -- the one outcome the lock exists to prevent --
#     and reading it as held is bounded by the age ceiling, which case 4 proves fires.
mkdir -p "$DESKTOP_LOCK_DIR"
printf '%s\n' "$$" > "$DESKTOP_LOCK_DIR/owner"
DESKTOP_LOCK_TIMEOUT=2 take 'truncated record' >/dev/null 2>&1
check "a truncated owner record with a live pid is not stolen" "$?" "1"
rm -rf "$DESKTOP_LOCK_DIR"

# 4. WEDGED BUT ALIVE. Liveness says "busy" forever; only the age ceiling frees
#    it. Backdate the lock's mtime so the owner is genuinely running and genuinely
#    past the ceiling.
PID="$(holder 30)"; sleep 1; assert_held_by "$PID"
touch -t 200001010000 "$DESKTOP_LOCK_DIR"
OUT="$(bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    DESKTOP_LOCK_MAX_HOLD=900 DESKTOP_LOCK_TIMEOUT=5 desktop_lock_acquire 'after wedge' && echo GOT
" 2>&1)"
check "a live but wedged owner is reclaimed on the age ceiling" "$(grep -c GOT <<<"$OUT")" "1"
kill -9 "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
rm -rf "$DESKTOP_LOCK_DIR"

# 5. RELEASE IS OWNER-ONLY. A process that never acquired must not be able to
#    free the lock out from under the peer it was waiting for.
PID="$(holder 10)"; sleep 1; assert_held_by "$PID"
bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    desktop_lock_release
" >/dev/null 2>&1
check "a non-holder's release does not free the lock" \
    "$([[ -d "$DESKTOP_LOCK_DIR" ]] && echo held || echo freed)" "held"
kill -9 "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
rm -rf "$DESKTOP_LOCK_DIR"

# 5b. RELEASE AFTER LOSING THE LOCK. Section 5 covers a process that NEVER held
#     the lock. This covers one that DID, was reclaimed while it was still
#     running, and then exits through its trap — the ordinary sequence, and the
#     one that was live in every repo sourcing this file: bash deleted the
#     PEER's lock while the python half left it alone.
PID="$(holder 10)"; sleep 1; assert_held_by "$PID"
bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    DESKTOP_LOCK_HELD=1
    desktop_lock_release
" >/dev/null 2>&1
check "a release by a process that LOST the lock does not free the peer's" \
    "$([[ -d "$DESKTOP_LOCK_DIR" ]] && echo held || echo freed)" "held"
kill -9 "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
rm -rf "$DESKTOP_LOCK_DIR"

# 5c. RELEASE ON AN UNREADABLE OWNER RECORD. Removing on doubt is the corrupting
#     case: we cannot show the lock is ours, so we leave it.
mkdir -p "$DESKTOP_LOCK_DIR"
bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    DESKTOP_LOCK_HELD=1
    desktop_lock_release
" >/dev/null 2>&1
check "a release does not delete a lock whose owner cannot be read" \
    "$([[ -d "$DESKTOP_LOCK_DIR" ]] && echo held || echo freed)" "held"
rm -rf "$DESKTOP_LOCK_DIR"

# 5d. A STALE lock directory is reclaimed even when it holds something besides its owner record.
#     Clearing it must not mean "unlink owner, then rmdir", which silently does nothing for any
#     other content: the reclaim is then a no-op, the next mkdir fails again, and acquire prints
#     "stale desktop lock" forever. run_bounded turns that hang into a red assertion (124, not 0).
mkdir -p "$DESKTOP_LOCK_DIR"
printf '999999\nThu Jan  1 00:00:00 1970\ndead owner\n' > "$DESKTOP_LOCK_DIR/owner"
touch "$DESKTOP_LOCK_DIR/.DS_Store"
DESKTOP_LOCK_TIMEOUT=5 run_bounded 20 take 'extra content' >"$TMP/extra.txt" 2>&1
check "a stale lock holding extra content is reclaimed" "$?" "0"
check "  and it took the lock rather than spinning" "$(grep -c GOT "$TMP/extra.txt")" "1"
rm -rf "$DESKTOP_LOCK_DIR"

# 6. THE TRAP. Normal exit must leave nothing behind.
bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    trap 'desktop_lock_release' EXIT
    desktop_lock_acquire 'clean exit'
" >/dev/null 2>&1
check "a clean exit releases via the trap" \
    "$([[ -d "$DESKTOP_LOCK_DIR" ]] && echo held || echo freed)" "freed"

hr
if [[ $FAIL -gt 0 ]]; then die "$FAIL failed, $PASS passed"; fi
ok "$PASS passed"
