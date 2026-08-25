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

section "desktop lock"

# 1. MUTUAL EXCLUSION. The lock must actually be held against a second taker.
PID="$(holder 30)"; sleep 1; assert_held_by "$PID"
bash -c "
    source '${GOH_DIR:-$GOH_ROOT}/tui/lib.sh'; source '$PWD/desktop_lock.sh'
    DESKTOP_LOCK_TIMEOUT=2 desktop_lock_acquire 'intruder'
" >/dev/null 2>&1
check "a second acquire is refused while a live peer holds it" "$?" "1"

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
