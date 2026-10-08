# shellcheck shell=bash
# bench_lock.sh -- a quiet machine on demand: a measurement holds off every goh gate on this host.
#
# HOUSE LIB, sourced. Every wall-clock number in docs/BACKLOG.md needs a quiet box (tools/quiet.sh), and
# for two days every one was taken at load 7-31 beside other sessions' gates (2026-10-06/08). The
# other locks here exclude something narrower: lib/desktop_lock/ the one screen, lib/tree_lock.sh
# one checkout's build tree. This one is a READER-WRITER lock over the whole host:
#
#   * every gate JOINS (goh_init, push_gate.sh, local_ci.sh): a file named by its pid in
#     $BENCH_LOCK_DIR/gates, written with bash builtins only -- no process spawned on the path
#     every commit takes -- and then a look for a measurement. One that finds a measurement
#     waits with NO file there: registered, a waiter is a running gate to the drain;
#   * a measurement takes it EXCLUSIVE (`bench_lock_exclusive`, tools/quiet.sh): `mkdir` of
#     $BENCH_LOCK_DIR/exclusive (atomic), then it waits for every registered gate to finish.
#     Either the gate sees the measurement and steps back, or the measurement sees the gate and
#     waits for it: each writes its own mark BEFORE it reads the other's;
#   * PHASE-FAIR: a gate that waits marks it in $BENCH_LOCK_DIR/waiting, and a new claim lets
#     every gate already waiting start first. Without it a series of measurements was one hold
#     to everyone else -- each claim followed the last release at once, and no gate queued behind
#     run N started before run N+1 (servers, 2026-10-08). Neither side starves: the gates that
#     arrive after the claim wait for it, and the claim waits only for those already queued.
#
# DEADLOCK is what the waiting side guards against, as desktop_lock.sh does:
#   1. A gate NESTED in a joined gate (this repo's suite runs gates inside its own push) must not
#      wait for a measurement that is waiting for its parent. A test environment strips GOH_* (so
#      an exported marker is not enough): a waiting gate walks its ancestors, and one that is a
#      registered gate -- or the measurement itself, whose own run starts gates -- lets it through.
#   2. A holder that died (SIGKILL, a crashed session): its pid is dead, or recycled -- the start
#      time recorded beside it no longer matches. Its claim is void and the next waiter reclaims.
#   3. A holder alive but wedged: past its max hold (GOH_BENCH_MAX_HOLD, default 900 s), void.
#      A hold blocks every session's commits, so it is short and says how long (BACKLOG 3.2):
#      `bench_lock_hold S` stamps it for a run of S seconds more, and a waiting gate prints its
#      end. The cap counts from the CLAIM: the drain and the settle block every gate too.
#   4. A holder killed between its `mkdir` and writing its owner file: an ownerless claim older
#      than a minute is void.
#
# WHY /tmp: the contended resource is the host, shared by every checkout and session; $TMPDIR is
# per-user and per-session under some harnesses, which would give each caller a private lock. The
# name does not start `goh-`, so the temp sweep (lib/prune_kept.py --backstop) never claims it.
#
# Pinned by tests/test_bench_lock.py.

BENCH_LOCK_DIR="${GOH_BENCH_LOCK_DIR:-/tmp/gates-of-heck-bench.lock}"
BENCH_LOCK_MAX_HOLD="${GOH_BENCH_MAX_HOLD:-900}"
BENCH_LOCK_ENTRY="" # this process's registration: the caller's cleanup removes it
BENCH_LOCK_HELD=0
BENCH_LOCK_BUSY="" # the gates a drain is still waiting for

# Line <n> of the exclusive holder's owner file -- pure bash: `sed` is rewritten on this machine
# (desktop_lock.sh has the incident), and an owner read as empty would void every live claim.
_bench_owner_field() {
    local line n=0
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        if [ "$n" -eq "$1" ]; then
            printf '%s' "$line"
            return 0
        fi
    done 2>/dev/null <"$BENCH_LOCK_DIR/exclusive/owner"
    return 1
}

# A process's start time, whitespace-normalised; empty when it does not exist.
_bench_start_time() {
    local raw
    raw="$(ps -o lstart= -p "$1" 2>/dev/null)" || return 0
    # shellcheck disable=SC2086  # word-splitting collapses ps's padding, the point
    set -- $raw
    printf '%s' "$*"
}

# The exclusive claim is void: no owner after a minute, or a dead, recycled or overdue owner.
_bench_exclusive_stale() {
    local pid start since hold now
    [ -d "$BENCH_LOCK_DIR/exclusive" ] || return 1
    if ! pid="$(_bench_owner_field 1)"; then
        [ -n "$(find "$BENCH_LOCK_DIR/exclusive" -maxdepth 0 -mmin +1 2>/dev/null)" ]
        return
    fi
    _bench_running "$pid" || return 0
    start="$(_bench_owner_field 2)" || return 0
    [ "$(_bench_start_time "$pid")" = "$start" ] || return 0
    since="$(_bench_owner_field 3)" || return 0
    hold="$(_bench_owner_field 4)" || return 0
    now="$(date +%s)"
    [ $((now - since)) -gt "$hold" ]
}

# `, until HH:MM at the latest` -- when the holder's claim goes void; empty when it cannot say.
_bench_ends() {
    local since hold end
    since="$(_bench_owner_field 3)" && hold="$(_bench_owner_field 4)" || return 0
    end=$((since + hold))
    printf ', until %s at the latest' "$(date -r "$end" +%H:%M 2>/dev/null || date -d "@$end" +%H:%M)"
}

# This process descends from the measurement, or from a gate already registered.
_bench_nested() {
    local p owner
    owner="$(_bench_owner_field 1 || true)"
    p="$PPID"
    while [ -n "$p" ] && [ "$p" -gt 1 ]; do
        { [ "$p" = "$owner" ] || [ -e "$BENCH_LOCK_DIR/gates/$p" ]; } && return 0
        p="$(ps -o ppid= -p "$p" 2>/dev/null)" || return 1
        p="${p//[[:space:]]/}"
    done
    return 1
}

# bench_lock_join <label> -- register this gate; wait while a measurement holds the host.
# Never fails a gate: a lock it cannot write is a lock it does not take part in.
bench_lock_join() {
    [ -n "${GOH_BENCH_JOINED:-}" ] && return 0 # an ancestor gate joined for this tree
    local gates="$BENCH_LOCK_DIR/gates" said="" through=""
    [ -d "$gates" ] || { mkdir -p "$gates" && chmod 1777 "$BENCH_LOCK_DIR" "$gates"; } 2>/dev/null || return 0
    while :; do
        # Held: look WITHOUT a file in gates/ -- a waiter registered there through its stale and
        # nested checks (a `ps` each) reads to the drain as a running gate, and on a loaded box
        # with dozens queued the drain never saw gates/ empty (2026-10-08).
        if [ -d "$BENCH_LOCK_DIR/exclusive" ] && [ -z "$through" ]; then
            if _bench_exclusive_stale; then
                rm -rf "$BENCH_LOCK_DIR/exclusive"
                continue
            fi
            if _bench_nested; then
                through=1
            else
                if [ -z "$said" ]; then # queued: the next claim lets this gate in first (_bench_yield)
                    mkdir -p "$BENCH_LOCK_DIR/waiting" 2>/dev/null && chmod 1777 "$BENCH_LOCK_DIR/waiting" 2>/dev/null
                    : >"$BENCH_LOCK_DIR/waiting/$$" 2>/dev/null
                    printf '· %s: waiting for a measurement to finish: %s%s\n' "$1" \
                        "$(_bench_owner_field 5 || echo 'one starting')" "$(_bench_ends)" >&2
                    said=1
                fi
                sleep 2
                continue
            fi
        fi
        BENCH_LOCK_ENTRY="$gates/$$"
        printf '%s\n' "$1" >"$BENCH_LOCK_ENTRY" 2>/dev/null || {
            BENCH_LOCK_ENTRY=""
            [ -z "$said" ] || rm -f "$BENCH_LOCK_DIR/waiting/$$"
            return 0
        }
        [ -d "$BENCH_LOCK_DIR/exclusive" ] && [ -z "$through" ] || break
        rm -f "$BENCH_LOCK_ENTRY" # a claim landed between the look and the mark: it goes first
        BENCH_LOCK_ENTRY=""
    done
    [ -z "$said" ] || rm -f "$BENCH_LOCK_DIR/waiting/$$"
    export GOH_BENCH_JOINED="$$"
}

# The gates already waiting when the host comes free start before a new claim takes it. Bounded:
# a waiter re-checks every 2 s, so one still marked after 10 s is not coming.
_bench_yield() {
    local f pid waiters="" end
    for f in "$BENCH_LOCK_DIR/waiting"/*; do
        [ -e "$f" ] || continue
        pid="${f##*/}"
        if _bench_running "$pid"; then waiters="$waiters $pid"; else rm -f "$f"; fi
    done
    [ -n "$waiters" ] || return 0
    printf '· bench lock: letting the gates already waiting start first:%s\n' "$waiters" >&2
    end=$(($(date +%s) + 10))
    for pid in $waiters; do
        while [ -e "$BENCH_LOCK_DIR/waiting/$pid" ] && _bench_running "$pid" && [ "$(date +%s)" -lt "$end" ]; do
            sleep 0.5
        done
    done
}

# The process runs: `kill -0` alone answers yes for a zombie, an exited gate its parent has not
# reaped yet, and the drain would wait on it until the parent did.
_bench_running() {
    kill -0 "$1" 2>/dev/null || return 1
    case "$(ps -o stat= -p "$1" 2>/dev/null)" in Z* | "") return 1 ;; esac
}

# Every registered gate has finished; a dead one's entry is removed. Sets BENCH_LOCK_BUSY.
_bench_drained() {
    local f pid
    BENCH_LOCK_BUSY=""
    for f in "$BENCH_LOCK_DIR/gates"/*; do
        [ -e "$f" ] || continue
        pid="${f##*/}"
        if _bench_running "$pid"; then
            BENCH_LOCK_BUSY="$BENCH_LOCK_BUSY $pid ($(head -n 1 "$f" 2>/dev/null))"
        else
            rm -f "$f"
        fi
    done
    [ -z "$BENCH_LOCK_BUSY" ]
}

# bench_lock_exclusive <label> [drain] -- hold the host: claim it, then wait for every gate to
# finish. Fails after GOH_BENCH_WAIT seconds (default 1800), naming what it waited on; a drain
# still busy `drain` seconds after the claim fails too, the claim released and BENCH_LOCK_BUSY set
# (every new gate waits behind a drain: tools/quiet.sh bounds it by its budget).
bench_lock_exclusive() {
    local label="$1" deadline said="" wait_s="${GOH_BENCH_WAIT:-1800}"
    BENCH_LOCK_BUSY=""
    deadline=$(($(date +%s) + wait_s))
    mkdir -p "$BENCH_LOCK_DIR/gates" 2>/dev/null && chmod 1777 "$BENCH_LOCK_DIR" "$BENCH_LOCK_DIR/gates" 2>/dev/null
    while :; do
        if [ ! -d "$BENCH_LOCK_DIR/exclusive" ]; then
            _bench_yield
            mkdir "$BENCH_LOCK_DIR/exclusive" 2>/dev/null && break
        fi
        if _bench_exclusive_stale; then
            rm -rf "$BENCH_LOCK_DIR/exclusive"
            continue
        fi
        if [ "$(date +%s)" -ge "$deadline" ]; then
            printf '✗ bench lock: another measurement holds the host: %s\n' "$(_bench_owner_field 5)" >&2
            return 1
        fi
        [ -n "$said" ] || printf '· bench lock: waiting for %s\n' "$(_bench_owner_field 5)" >&2
        said=1
        sleep 2
    done
    printf '%s\n%s\n%s\n%s\n%s\n' "$$" "$(_bench_start_time "$$")" "$(date +%s)" \
        "$BENCH_LOCK_MAX_HOLD" "$label (pid $$)" >"$BENCH_LOCK_DIR/exclusive/owner"
    BENCH_LOCK_HELD=1
    said=""
    if [ -n "${2:-}" ] && [ "$2" -lt $((deadline - $(date +%s))) ]; then
        wait_s="$2"
        deadline=$(($(date +%s) + wait_s))
    fi
    until _bench_drained; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            printf '✗ bench lock: gates still running after %ss:%s\n' "$wait_s" "$BENCH_LOCK_BUSY" >&2
            bench_lock_release
            return 1
        fi
        [ -n "$said" ] || printf '· bench lock: waiting for running gates:%s\n' "$BENCH_LOCK_BUSY" >&2
        said=1
        sleep 2
    done
}

# Seconds since this process claimed the host.
bench_lock_spent() {
    local since
    since="$(_bench_owner_field 3)" || return 1
    printf '%s' "$(($(date +%s) - since))"
}

# bench_lock_hold <seconds> -- stamp this process's claim to end that long from now; refused
# (status 1, the claim unchanged) when that is past the cap counted from the claim.
bench_lock_hold() {
    [ "$BENCH_LOCK_HELD" = 1 ] && [ "$(_bench_owner_field 1)" = "$$" ] || return 1
    local spent
    spent="$(bench_lock_spent)" || return 1
    [ $((spent + $1)) -le "$BENCH_LOCK_MAX_HOLD" ] || return 1
    printf '%s\n%s\n%s\n%s\n%s\n' "$$" "$(_bench_owner_field 2)" "$(_bench_owner_field 3)" \
        "$((spent + $1))" "$(_bench_owner_field 5)" >"$BENCH_LOCK_DIR/exclusive/owner.new" &&
        mv "$BENCH_LOCK_DIR/exclusive/owner.new" "$BENCH_LOCK_DIR/exclusive/owner"
}

# Release a claim this process holds; a no-op otherwise, so an EXIT trap can call it always.
bench_lock_release() {
    [ "$BENCH_LOCK_HELD" = 1 ] || return 0
    if [ "$(_bench_owner_field 1)" = "$$" ]; then rm -rf "$BENCH_LOCK_DIR/exclusive"; fi
    BENCH_LOCK_HELD=0
}
