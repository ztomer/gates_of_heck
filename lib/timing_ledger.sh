# shellcheck shell=bash
# lib/timing_ledger.sh -- a span's wall, cpu-s and load for the timing ledger (GOH.GATEBUDGET).
#
#   . "$GOH/lib/timing_ledger.sh"
#   goh_span_start                        # the span's START: wall clock, reaped-children CPU, load
#   ... the work, in this shell or its children ...
#   goh_span_record KIND REPO OK [--timings FILE] [--field K=V]...
#                                         # the span's END: one row, subject "<REPO> <KIND>"
#
# One implementation of the shell side, so the push gate and the land measure a span
# the same way (lib/budget.py KINDS names each kind's start and end). CPU is bash's own `times`:
# the user+system time of every child this shell has reaped (and theirs), so a delta around the work
# is the work's tree; a daemon nobody reaps is not in it. The record is written by
# lib/timing_ledger.py, which never fails: a ledger that cannot be written leaves a gate's exit alone.
# Sourced, never run. Pinned by tests/test_timing_ledger.py.

# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/timing_ledger.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# Sets _goh_cpu_now to the seconds of CPU the children of THIS shell have used so far, one decimal
# ("0.0" when unreadable). A global, never printed for a `$( )`: `times` in a command substitution
# is the substitution's own subshell, whose children are none (the first version read 0.0 always).
. "${BASH_SOURCE[0]%/*}/tmp_root.sh"   # goh_tmp: every temp root under $TMPDIR/goh.noindex, never Spotlight-indexed (GOH.TMPNOINDEX)
goh_children_cpu() {
    local f
    _goh_cpu_now="0.0"
    f="$(mktemp "$(goh_tmp)/goh-times.XXXXXX")" || return 0
    times >"$f"
    _goh_cpu_now="$(awk 'NR == 2 { for (i = 1; i <= 2; i++) { gsub(",", ".", $i); split($i, a, /[ms]/); s += a[1] * 60 + a[2] } }
                        END { printf "%.1f", s + 0 }' "$f")"
    rm -f "$f"
}

# The box's 1-minute load average ("" when unreadable).
goh_load1() {
    local l
    l="$(sysctl -n vm.loadavg 2>/dev/null | awk '{ print $2 }')"
    [ -n "$l" ] || l="$(awk '{ print $1 }' /proc/loadavg 2>/dev/null)"
    printf '%s' "$l"
}

# The repository's own name, whichever worktree DIR is: the directory holding the common git dir
# (a lane's `wt` worktree is `ztools`'s ledger subject, never `wt`).
goh_repo_id() {
    local common
    common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || common=""
    if [ -n "$common" ]; then basename "$(dirname "$common")"; else basename "$1"; fi
}

# The push gate's machine-wide slot wait (GOH.LOADSOURCES): its span starts AFTER the slot, so the
# wait was in no record and a replay of the day's admissions (lib/admit_replay.py) could only put a
# lower bound on it. goh_slot_asked before gate_slot_acquire, goh_slot_taken after; the next push
# row carries `slot_wait_s`, `slot_asked_at` (epoch) and `slot_class` (the class it waited as,
# GOH.SLOTPRIORITY), once.
goh_slot_asked() { _goh_slot_asked="${EPOCHREALTIME/,/.}"; _goh_slot_wait=""; }
goh_slot_taken() {
    [ -n "${_goh_slot_asked:-}" ] || return 0
    _goh_slot_wait="$(awk -v a="$_goh_slot_asked" -v b="${EPOCHREALTIME/,/.}" 'BEGIN { printf "%.1f", b - a }')"
}

# The push gate's row: REPO's `push` span ends with STATUS (0 passed) on COMMIT.
goh_push_row() {
    local ok=0 slot=()
    [ "$2" -ne 0 ] || ok=1
    [ -z "${_goh_slot_wait:-}" ] ||
        slot=(--field "slot_wait_s=$_goh_slot_wait" --field "slot_asked_at=$_goh_slot_asked"
              --field "slot_class=${GATE_SLOT_CLASS:-work}")
    _goh_slot_wait=""   # one push row carries the wait: the slot is taken once per push
    goh_span_record push "$(goh_repo_id "$1")" "$ok" ${GOH_TIMINGS:+--timings "$GOH_TIMINGS"} \
        --field "sha=${3:0:12}" "${slot[@]}"
}

goh_span_start() {
    _goh_span_t0="${EPOCHREALTIME/,/.}"
    goh_children_cpu
    _goh_span_cpu0="$_goh_cpu_now"
    _goh_span_load="$(goh_load1)"
}

goh_span_record() {
    local kind="$1" repo="$2" ok="$3"
    shift 3
    [ -n "${_goh_span_t0:-}" ] || return 0   # no start: nothing was measured
    local now cpu1 wall cpu args=()
    now="${EPOCHREALTIME/,/.}"
    goh_children_cpu
    cpu1="$_goh_cpu_now"
    wall="$(awk -v a="$_goh_span_t0" -v b="$now" 'BEGIN { printf "%.1f", b - a }')"
    cpu="$(awk -v a="${_goh_span_cpu0:-0}" -v b="$cpu1" 'BEGIN { printf "%.1f", b - a }')"
    [ -z "${_goh_span_load:-}" ] || args+=(--load "$_goh_span_load")
    _goh_span_t0=""   # one record per span
    "$GOH_PY" "$GOH/lib/timing_ledger.py" record --kind "$kind" --repo "$repo" --wall "$wall" \
        --cpu "$cpu" --ok "$ok" "${args[@]}" "$@" >/dev/null 2>&1 </dev/null || true
}
