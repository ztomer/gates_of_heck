#!/usr/bin/env bash
# gpu_lock.sh -- machine-wide mutual exclusion for the GPU (an osaurus server, a model run, and
# any measurement a model run would corrupt). Bash half; lib/gpu_lock/gpu_lock.py is the Python
# half and ztools' rust/src/ztools/eval/gpu_lock.rs the Rust one: ONE lock, one record format.
#
# CANONICAL HERE since 2026-10-10. It was born in ztools (tools/gpu_lock.sh), modelled on the
# desktop lock and DELIBERATELY NOT SHARING ITS LOCK: the desktop and the GPU are different
# resources, and a screenshot and an eval have no reason to exclude each other. It moved to the
# house lib beside lib/desktop_lock/ because the GPU is a machine resource every repo's
# measurements contend for, not one repo's: a census whose time columns a model run would load,
# or whose wallpaper composite would slow a decode-rate reading, takes it too. The environment
# names keep their ZTOOLS_ prefix because the Rust client reads them.
#
# WHAT COUNTS AS "TAKING THE GPU" -- take this lock if you do any of:
#   - start, stop, restart or quit the osaurus app or `osaurus serve`
#   - measure a model (prefill rate, decode rate, task latency, memory)
#   - load model weights, by any route, on a machine sized for one at a time
#
# WHY IT IS A HARD LOCK, and why the damage is worse than a wasted run. Several
# agent sessions now run concurrently on this Mac. Every model here is 4-35GB
# resident, and a second server does not queue behind the first -- it loads its
# OWN copy, which means eviction, swapping, and requests the server cancels
# itself with HTTP 499 request_cancelled. From the client that is
# indistinguishable from a slow model. It has already happened: a stray second
# server recorded qwen3.8-27b at 0.1 tok/s decode with a 423s cold start.
#
# AND THE MACHINE'S OWN CONTENTION GUARD CANNOT SEE THIS. eval/samples.py keeps a
# list of samples and estimates from the median of the last SAMPLE_WINDOW=5 CLEAN
# ones, which sounds like it outvotes a bad reading -- and does, for the pressure
# it can measure. But machine_is_uncontended() gates on SWAP and COMPRESSOR only,
# read from psutil and vm_stat. A peer session saturating the GPU moves neither, so
# its interference is tagged CLEAN and enters the median as though the box were
# quiet. The median cannot outvote a reading it believes is good.
#
# And the median only protects a model that HAS history. A new or thinly-sampled
# model's estimate IS its one sample, so first measurements -- the ones you are
# most likely to take casually -- are the exposed case. This lock is what the
# swap/compressor guard is blind to.
#
# WHY `mkdir` AND NOT `flock`. macOS ships no flock(1). `mkdir` is atomic on every
# POSIX filesystem -- it either creates the directory or fails, with no window
# between the test and the create. A `[[ -e ]]` test followed by `mkdir` is NOT
# equivalent and races.
#
# WHY /tmp AND NOT $TMPDIR. The contended resource is the one GPU in this Mac,
# shared by every checkout, worktree and agent session on it. $TMPDIR is per-user
# on macOS and per-SESSION under some agent harnesses, which would hand each
# caller a private lock and no exclusion whatsoever. /tmp is machine-wide and
# stable, which is the property the lock's correctness rests on.
#
# DEADLOCK IS THE FAILURE MODE THIS FILE IS MOST CAREFUL ABOUT, because an agent
# session can die in more ways than it can exit cleanly. FOUR independent
# releases, so no single failure can wedge every eval on the machine:
#
#   1. THE TRAP, on a clean exit, failure or Ctrl-C. Covers almost everything.
#   2. OWNER LIVENESS, for SIGKILL and for a crashed or force-quit agent, which
#      never run a trap at all. The owner PID is probed with `kill -0`.
#      PID ALONE IS NOT ENOUGH: PIDs are recycled, and a recycled PID reads as
#      "alive" forever, which is a permanent deadlock that looks like a busy
#      peer. So the owner's START TIME is recorded alongside the PID and must
#      also match -- a different process wearing the same number is not the owner.
#   3. A MAX HOLD AGE, for an owner that is alive but WEDGED (a hung osascript, a
#      server that will neither answer nor die, an agent blocked on input).
#   4. INHERITANCE, so a wrapper that holds the lock can run a child that also
#      asks for it without the child blocking on its own parent forever.
#
# THE CEILING MEASURES PROGRESS, NOT DURATION, and that distinction is the whole
# reason this file differs from the desktop lock. An honest desktop run is under
# two minutes, so 900s of wall clock is proof of a wedge. An honest EVAL run is
# hours: the sweep's per-model ceiling is 4h and rerun_truncated raises it to 10h.
# A wall-clock ceiling short enough to catch a wedge would reclaim the lock out
# from under a perfectly healthy 6-hour measurement and hand it to a peer that
# then restarts the server -- causing precisely the corruption this file exists to
# prevent. So the holder HEARTBEATS (gpu_lock_heartbeat, called after each unit of
# work) and the ceiling is measured from the last beat. A run that is still making
# progress never expires; a run that has stopped making progress expires whether
# or not its process is still alive.
#
# The bias is therefore the OPPOSITE of the desktop lock's. There, reclaiming
# wrongly costs one confused screenshot, so it reclaims eagerly. Here, reclaiming
# wrongly corrupts a tracked capability record that nobody will notice is wrong,
# so a waiter that cannot get the lock FAILS AND SAYS WHO HOLDS IT rather than
# waiting long enough to be tempted into stealing it.
#
# Requires the caller to have sourced gates_of_heck's tui/lib.sh (info/ok/warn/die).
#
# Usage:
#     source "$GOH_DIR/lib/gpu_lock/gpu_lock.sh"
#     trap 'gpu_lock_release' EXIT INT TERM
#     gpu_lock_acquire "osaurus_one.sh --restart"

# That requirement is ENFORCED, not just documented. tui/lib.sh was deleted by
# 29ddbac along with the Python TUI while five scripts still sourced it, and the
# failure was silent in the worst possible way: `die` at the acquire timeout
# became "die: command not found", which under a `while` loop without `set -e`
# is not an abort but a CONTINUE. gpu_lock_acquire could therefore never time
# out on any machine -- it spun forever instead of refusing, and the ztools test
# suite hung at 24% because of it.
#
# A precondition that only exists as a comment is a precondition that will be
# broken. Checked here, at source time, where the message can still be read.
# The requirement is now SELF-SATISFYING rather than merely documented. A guard
# that only returns non-zero is not enough here: `return` from a sourced file
# returns from the SOURCE, and the caller carries on regardless. Defining
# fallbacks means `die` is always a real abort, whatever the caller sourced.
for _gpu_lock_helper in info ok warn err; do
    declare -F "$_gpu_lock_helper" >/dev/null 2>&1 || eval "
        $_gpu_lock_helper() { printf '%s\n' \"\$*\" >&2; }"
done
declare -F die >/dev/null 2>&1 || die() { err "$*"; exit "${2:-1}"; }
unset _gpu_lock_helper

# The machine-wide default. ZTOOLS_GPU_LOCK_DIR exists so the gate is testable in
# BOTH directions -- a test can prove exclusion and prove reclamation without
# touching the real lock a concurrent session may be holding. Production never
# sets it; a test asserts the default is this path, so the seam cannot quietly
# become the norm.
GPU_LOCK_DIR="${ZTOOLS_GPU_LOCK_DIR:-/tmp/mac-osaurus-gpu.lock}"
# Short on purpose. A peer holding this lock is usually mid-eval and will hold it
# for hours, so waiting is theatre: the useful answer is "another session owns the
# GPU, here is which one". Callers that genuinely want to queue raise it.
GPU_LOCK_TIMEOUT="${GPU_LOCK_TIMEOUT:-60}"
# Time since the last HEARTBEAT, not since acquisition -- see the header. The
# longest legitimate gap between beats is one task, and the per-task timeout
# derived from measured rates is ~7055s, so 4h leaves roughly 2x headroom.
GPU_LOCK_MAX_IDLE="${GPU_LOCK_MAX_IDLE:-14400}"
# Set only once actually acquired, so release is a no-op for a process that never
# held the lock and can be called unconditionally from an EXIT trap.
GPU_LOCK_HELD=0
# Set when a live ancestor already holds it: this process must neither block on it
# nor release it, because it does not own it.
GPU_LOCK_INHERITED=0

# The owner file is three lines: pid, the process start time, and a human label.
#
# DELIBERATELY NO `sed`. On this machine sed is rewritten out from under scripts
# -- an rtk Bash hook in some sessions, an `sd` alias in the interactive shell --
# and `sed -n 2p` comes back as "invalid value for --max-replacements". A lock
# whose staleness check silently returns EMPTY treats every live owner as dead and
# grants the lock to everyone, which is worse than having no lock at all because
# it still prints reassuring "acquired" messages. Pure bash has nothing to rewrite.
_gpu_lock_field() {
    local line n=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        n=$((n + 1))
        if [[ $n -eq $1 ]]; then printf '%s' "$line"; return 0; fi
    done < "$GPU_LOCK_DIR/owner" 2>/dev/null
    return 1
}

# A process's start time, used to tell the real owner from a recycled PID.
# Empty when the process does not exist, which the caller treats as dead.
#
# THE NORMALISATION IS PART OF THE CROSS-LANGUAGE CONTRACT. gpu_lock.py beside this must
# produce a byte-identical string for the same process, or each half reads the
# other's records as impostors and silently grants a lock the peer is holding --
# which is exactly what an untested first version of the desktop lock did. `ps`
# pads single-digit days ("Aug  1" vs "Aug 18"), so anything short of collapsing
# whitespace runs drifts on two days in three. Word-splitting then rejoining with
# "$*" collapses runs AND trims the ends; Python's str.split() with no argument
# does the same, which is why both sides are written that way rather than with
# `tr -s`. A test compares the two implementations' output on a live PID.
#
# `ps` IS ALIASED TO `procs` IN THE INTERACTIVE SHELL ON THIS MACHINE, the same
# rewriting hazard the house lock documents for `sed`. It is harmless HERE only
# because aliases do not survive into a script's own shell -- verified, not
# assumed: `bash -c 'command -v ps'` reports /bin/ps. If that ever changes, this
# returns empty for every process and the recycled-PID defence disappears
# SILENTLY, because an empty recorded start time is treated as "no fingerprint
# available, accept the PID". Do not reach for a bare `ps` in an interactive
# context and conclude it works.
_gpu_lock_start_time() {
    local raw
    raw="$(ps -o lstart= -p "$1" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    set -- $raw
    printf '%s' "$*"
}

# Epoch mtime of a path. BSD stat and GNU stat spell this differently and the
# repo's CI runs on Linux, so a BSD-only spelling would make the ceiling silently
# unmeasurable there -- and an unmeasurable ceiling reads as "never expired".
#
# Each spelling's OUTPUT is validated rather than its exit status, because the
# other platform's stat does not reliably fail on the wrong flag: GNU `stat -f`
# means --file-system, where `%m` is a mount point, so a plain `||` chain can
# come back with "/" and the arithmetic below then errors instead of degrading.
_gpu_lock_mtime() {
    local out
    out="$(stat -f %m "$1" 2>/dev/null || true)"
    [[ "$out" =~ ^[0-9]+$ ]] || out="$(stat -c %Y "$1" 2>/dev/null || true)"
    [[ "$out" =~ ^[0-9]+$ ]] || out=0
    printf '%s' "$out"
}

# A lock directory with no readable owner file is stale too: it means a run died
# between the mkdir and the write, leaving a lock nobody could ever release.
_gpu_lock_owner_alive() {
    [[ -r "$GPU_LOCK_DIR/owner" ]] || return 1
    local pid recorded current
    pid="$(_gpu_lock_field 1)"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    # PID is live -- but is it the SAME process, or a recycled number?
    recorded="$(_gpu_lock_field 2)"
    current="$(_gpu_lock_start_time "$pid")"
    [[ -n "$recorded" && "$recorded" != "$current" ]] && return 1
    return 0
}

# Has the holder stopped making progress? Measured from the last heartbeat, which
# is the directory's mtime, so an owner that is alive but wedged still expires and
# an owner that is slow but working never does.
_gpu_lock_expired() {
    local beat now
    beat="$(_gpu_lock_mtime "$GPU_LOCK_DIR")"
    [[ "$beat" == "0" ]] && return 1
    now="$(date +%s)"
    (( now - beat >= GPU_LOCK_MAX_IDLE ))
}

_gpu_lock_owner_label() {
    local label
    label="$(_gpu_lock_field 3)"
    [[ -n "$label" ]] && printf '%s' "$label" || printf 'an unknown run'
}

# Print the label of a LIVE holder that is neither this process nor an ancestor
# that acquired on our behalf; print nothing when the GPU is ours or free.
#
# This is the predicate the osascript quit call sites consult. They do not take
# the lock -- they are deep inside unrelated tools (a twitter summariser, a
# weekend planner) whose job is not to queue behind an eval -- they simply must
# not quit a server another session is measuring against.
gpu_lock_foreign_holder() {
    _gpu_lock_owner_alive || return 0
    local pid
    pid="$(_gpu_lock_field 1)"
    [[ "$pid" == "$$" ]] && return 0
    [[ -n "${ZTOOLS_GPU_LOCK_OWNER:-}" && "$pid" == "$ZTOOLS_GPU_LOCK_OWNER" ]] && return 0
    _gpu_lock_owner_label
}

# Print the label of whoever holds the GPU, ours included; nothing when free.
gpu_lock_holder() {
    _gpu_lock_owner_alive || return 0
    _gpu_lock_owner_label
}

# Take the lock. Dies rather than continuing unlocked -- continuing is the
# corrupting case this file exists to prevent.
gpu_lock_acquire() {
    local label="${1:-gpu run}"
    local waited=0
    local announced=0
    # An ancestor already holds it on our behalf: adopt, do not block on it and do
    # not claim ownership, or the child's release would free the parent's lock.
    if [[ -n "${ZTOOLS_GPU_LOCK_OWNER:-}" ]] && _gpu_lock_owner_alive \
       && [[ "$(_gpu_lock_field 1)" == "$ZTOOLS_GPU_LOCK_OWNER" ]]; then
        GPU_LOCK_INHERITED=1
        info "gpu already held by $( _gpu_lock_owner_label ) (inherited)"
        return 0
    fi
    while ! mkdir "$GPU_LOCK_DIR" 2>/dev/null; do
        if ! _gpu_lock_owner_alive; then
            warn "stale gpu lock from $( _gpu_lock_owner_label ) — reclaiming"
            rm -rf "$GPU_LOCK_DIR"
            continue
        fi
        if _gpu_lock_expired; then
            warn "gpu held with no progress for ${GPU_LOCK_MAX_IDLE}s by $( _gpu_lock_owner_label ) — wedged, reclaiming"
            rm -rf "$GPU_LOCK_DIR"
            continue
        fi
        if [[ $announced -eq 0 ]]; then
            info "the gpu is held by $( _gpu_lock_owner_label )"
            announced=1
        fi
        if [[ $waited -ge $GPU_LOCK_TIMEOUT ]]; then
            die "gpu still held by $( _gpu_lock_owner_label ) after ${GPU_LOCK_TIMEOUT}s — that session is measuring; do not restart osaurus under it"
        fi
        sleep 1
        waited=$((waited + 1))
    done
    printf '%s\n%s\n%s\n' \
        "$$" "$(_gpu_lock_start_time $$)" "$label (pid $$)" \
        > "$GPU_LOCK_DIR/owner"
    GPU_LOCK_HELD=1
    export ZTOOLS_GPU_LOCK_OWNER="$$"
    [[ $announced -eq 1 ]] && ok "gpu acquired after ${waited}s"
    return 0
}

# Say "still working" so the idle ceiling does not reclaim a healthy long run.
# A no-op unless we actually hold the lock, so an unheld caller cannot keep a
# peer's expired lock alive.
gpu_lock_heartbeat() {
    [[ $GPU_LOCK_HELD -eq 1 ]] || return 0
    touch "$GPU_LOCK_DIR" 2>/dev/null || true
}

# Release ONLY a lock this process actually holds. Without the guard, a run that
# died while waiting would delete the lock belonging to the peer it waited for,
# and an inheriting child would free its parent's.
gpu_lock_release() {
    [[ $GPU_LOCK_HELD -eq 1 ]] || return 0
    GPU_LOCK_HELD=0
    unset ZTOOLS_GPU_LOCK_OWNER
    rm -rf "$GPU_LOCK_DIR"
}
