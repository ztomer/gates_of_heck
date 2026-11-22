# shellcheck shell=bash
# gate_slot.sh -- a machine-wide limit on how many gates run their heavy steps at once.
#
# HOUSE LIB, sourced by EVERY full-gate entry point after tui/lib.sh (or info/ok/err defined):
# gates/push_gate.sh, gates/local_ci.sh (what every `gate.sh --full` execs), gates/round.sh's
# preflight, and the starter tools/gate.sh install.sh writes. The mechanism, the incident and the
# nesting rule are in lib/gate_slot.py's header; this is the shell's half:
#
#     gate_slot_acquire <repo name> [class] [max wait s]   # blocks until a slot is free; announces
#         # the wait and its end. class: short | land | ship | lane | work (lib/gate_lanes.py CLASSES,
#         # GOH.SLOTFAIR, GOH.SLOTPRIORITY);
#         # none given, GOH_GATE_CLASS (gates/commit_gate.sh sets `short` for the gate.sh it runs),
        # else `lane` in a loop lane's worktree (GOH.LANECLASS),
#         # consumed by the acquire so nothing this gate starts inherits it; neither: work. A max
#         # wait (seconds, the memory wait counted inside it) bounds it: past it, the holders are
#         # named and it RETURNS 75 with no slot taken (tools/pytest.sh, GOH.ADMITSUITE); none: unbounded
#     gate_slot_release               # the caller's cleanup calls it on every exit path
#     gate_slot_acquire_for_caller <repo name>
#         # structural.sh --full: when its PARENT is a `tools/gate.sh`, take the slot FOR that
#         # gate.sh, held until it ends (the consumer copies that predate the slot all call
#         # structural.sh first, and nothing else of goh's); otherwise take none
#
# GOH_GATE_SLOTS=N   how many gates at once (default: one per eight cores, at least one;
#                    `python3 lib/gate_slot.py default` prints it). `0` turns the limit off.
# GOH_GATE_SLOT_DIR  the lock files (default ~/.cache/goh/slots): machine-wide on purpose.
# GOH_GATE_WORKERS   set BY the admission: this gate's share of the cores, with every runner's own
#                    variable beside it (lib/worker_share.py RUNNER_ENV, GOH.POOLSHARE).
# GOH_GATE_SLOT      set BY a gate holding a slot, "<slot file> <owner pid>", so a gate nested
#                    inside it takes none (never set it by hand: one naming a pid that is not an
#                    ancestor is ignored).
#
# The wait is reported apart from the gate's time: every step ceiling (GOH_LCI_TIMEOUT,
# GOH_STEP_TIMEOUT) belongs to a step the gate starts AFTER this returns, so a wait can never spend
# a step's budget. Pinned by tests/test_gate_slot.py.

_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
# shellcheck source=lib/lock_order.sh
. "${BASH_SOURCE[0]%/*}/lock_order.sh" || exit 1
. "${BASH_SOURCE[0]%/*}/tmp_root.sh"   # goh_tmp: every temp root under $TMPDIR/goh.noindex, never Spotlight-indexed (GOH.TMPNOINDEX)   # the one lock order (GOH.LOCKORDER)
# shellcheck source=lib/worker_share.sh
. "${BASH_SOURCE[0]%/*}/worker_share.sh" || exit 1
# shellcheck source=lib/pid_owner.sh
. "${BASH_SOURCE[0]%/*}/pid_owner.sh" || exit 1   # the holder is signalled by its stamp (GOH.STRAYSIGNAL)   # how wide an admitted gate runs (GOH.POOLSHARE)
GATE_SLOT_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gate_slot.py" # beside this file
GATE_SLOT_PID="" # the holder process (lib/gate_slot.py hold); gate_slot_release ends it
GATE_SLOT_STAMP="" # its goh_pid_stamp: the release signals only that process, never a reused pid
GATE_SLOT_CLASS="" # the class the last acquire asked by (GOH.SLOTPRIORITY): the push row records it

gate_slot_acquire() {
    _gate_slot_take "${1:-gate}" "$$" "" "${2:-}" "${3:-}"
}

# A full gate's entry is the consumer's own tools/gate.sh, and this shell is its child: the slot
# must outlive this shell and end with the gate.sh. Anything else calling structural.sh --full (a
# terminal, a CI step, a gate runner already holding a slot) is not a gate.sh to hold one for --
# a slot held "for" an interactive shell would be held until the terminal closed.
gate_slot_acquire_for_caller() {
    local caller="$PPID" cmd
    cmd="$(ps -o command= -p "$caller" 2>/dev/null || true)"
    case " $cmd " in
        *" tools/gate.sh "* | *"/tools/gate.sh "*) ;;
        *) return 0 ;;
    esac
    _gate_slot_take "${1:-gate}" "$caller" --for-caller ""
}

_gate_slot_take() { # <repo> <owner pid> <"" | --for-caller> <class | ""> <max wait s | "">
    local repo="$1" owner="$2" mode="$3" class="${4:-${GOH_GATE_CLASS:-}}" n="${GOH_GATE_SLOTS-}"
    local max="${5:-}"
    unset GOH_GATE_CLASS   # this admission's word: never inherited by what the gate starts
    local dir="${GOH_GATE_SLOT_DIR:-$HOME/.cache/goh/slots}"
    local helper="$GATE_SLOT_HELPER" fifo line idx total waited path pid mech
    # Undeclared: `lane` when this gate runs in a loop lane's worktree, else `work` (GOH.LANECLASS;
    # lib/gate_lanes.py default_class). Derived from where it runs, never asked of each lane.
    # With the slots off (GOH_GATE_SLOTS=0) nothing is admitted, so nothing is asked.
    [ -n "$class" ] || [ "$n" = 0 ] || class="$("$GOH_PY" -S "$helper" default-class --checkout "$PWD")" \
        || { err "gate slot: cannot derive the gate's class (lib/gate_slot.py default-class)"; exit 1; }
    class="${class:-work}"
    case "$class" in
        short | land | ship | lane | work) ;;
        *) err "GOH_GATE_CLASS must be short, land, ship, lane or work (got '$class')"
           exit 2 ;;
    esac
    GATE_SLOT_CLASS="$class"   # the push row's `slot_class` (lib/timing_ledger.sh goh_push_row)
    case "$max" in
        *[!0-9]*) err "gate slot: a max wait is whole seconds (got '$max')"
                  exit 2 ;;
    esac
    case "$n" in
        "") ;;
        0) info "gate slots: off (GOH_GATE_SLOTS=0) -- this gate runs beside any number of others"
           return 0 ;;
        *[!0-9]*) err "GOH_GATE_SLOTS must be a non-negative integer of concurrent gates (got '$n')"
                  exit 2 ;;
    esac
    mkdir -p "$dir"
    if line="$("$GOH_PY" -S "$helper" nested --dir "$dir")"; then
        info "gate slot: inside a gate that holds $line -- not taking another"
        return 0
    fi
    goh_lock_check gate_slot || exit 78   # never wait on a slot inside a later lock's holder
    fifo="$(mktemp -u "$(goh_tmp)/goh-gate-slot.XXXXXX")"
    mkfifo "$fifo"
    "$GOH_PY" -S "$helper" hold --dir "$dir" ${n:+--slots "$n"} --owner "$owner" --repo "$repo" \
        --handshake "$fifo" --class "$class" ${max:+--max-wait "$max"} ${mode:+"$mode"} </dev/null &
    pid=$!
    local stamp
    stamp="$(goh_pid_stamp "$pid" || true)"   # an empty stamp (the holder already gone) kills nothing
    # Read-write, so the open never blocks and a holder that died before writing cannot hang this
    # shell: the read times out, and the holder's liveness is asked between reads.
    # A CHARACTER AT A TIME (GOH.CI107): a `read -t` that times out part way through a line has
    # already consumed those bytes, so a line-at-a-time loop restarted mid-line and read the tail as
    # the fields -- the wait went unreported and GOH_GATE_SLOT lost its path (v0.107.0's hosted run;
    # a 1 ms timeout split 404 of 2000 single-write lines here). One character is consumed whole or
    # not at all, under bash 3.2 (a sourced library stays 3.2-safe) and 5.
    # Pinned by tests/test_gate_slot_handshake.py, which also refuses the class in every bash file.
    local hs="" c
    exec 7<>"$fifo"
    while :; do
        if IFS= read -r -n 1 -t 1 c <&7; then
            [ -n "$c" ] || break   # the newline: the handshake is whole
            hs+="$c"
        elif ! kill -0 "$pid" 2>/dev/null; then
            exec 7<&-
            rm -f "$fifo"
            err "gate slot: the slot holder (lib/gate_slot.py) exited without a slot"
            exit 1
        fi
    done
    exec 7<&-
    rm -f "$fifo"
    if [ "${hs%% *}" = expired ]; then
        wait "$pid" 2>/dev/null || true
        err "gate slot: still held after ${max}s waiting (class $class) -- no slot taken: ${hs#expired }"
        return 75
    fi
    read -r idx total waited path mech <<<"$hs"
    case "$idx:$total:$waited" in
        *[!0-9:]* | :* | *::* | *:) path="" ;;
    esac
    if [ -z "$path" ]; then
        goh_pid_kill TERM "$stamp" || true
        line="gate slot: the slot holder's handshake is not"
        err "$line '<slot> <slots> <seconds waited> <path> <watch>': '$hs'"
        exit 1
    fi
    # WHICH watch took the slot: the kernel event (kqueue on macOS, a pidfd on Linux) or the pid
    # poll where the kernel has neither. Named, because a watch that cannot be seen cannot be
    # proved to have run -- the Linux pidfd branch shipped in v0.39.0 without ever being run.
    [ -n "$mech" ] || mech="poll"
    export GOH_GATE_SLOT="$path $owner"
    goh_lock_mark gate_slot
    # THE WIDTH (GOH.POOLSHARE): cores / the gates admitted now, this one included, exported in the
    # variables every runner it starts reads. Counted afresh: an inherited share is another gate's.
    goh_workers_export --admitted --dir "$dir" || { err "gate slot: cannot size this gate's workers (lib/worker_share.py)"; exit 1; }
    local whose="(GOH_GATE_SLOTS, class $GATE_SLOT_CLASS)"
    if [ -n "$mode" ]; then
        # Never this shell's to release: the holder ends with the gate.sh it was taken for.
        whose="for tools/gate.sh (pid $owner), held until it ends"
    else
        GATE_SLOT_PID="$pid"
        GATE_SLOT_STAMP="$stamp"
    fi
    local word="gate slot"
    case "${path##*/}" in short-*) word="short-lane gate slot" ;; esac
    if [ "$waited" -gt 0 ]; then
        ok "$word $((idx + 1))/$total taken after ${waited}s waiting (class $class) -- the step limits count from here"
        [ -z "$mode" ] || info "  $whose"
    else
        info "$word $((idx + 1))/$total taken $whose"
    fi
    info "  owner watch: $mech"
    info "  workers: $GOH_GATE_WORKERS (cores / gates admitted now, GOH.POOLSHARE)"
}

gate_slot_release() {
    if [ -n "$GATE_SLOT_PID" ]; then
        goh_pid_kill TERM "$GATE_SLOT_STAMP" || true
        wait "$GATE_SLOT_PID" 2>/dev/null || true
        goh_lock_drop gate_slot
    fi
    GATE_SLOT_PID=""
    GATE_SLOT_STAMP=""
}
