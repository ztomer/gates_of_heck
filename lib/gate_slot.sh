# shellcheck shell=bash
# gate_slot.sh -- a machine-wide limit on how many gates run their heavy steps at once.
#
# HOUSE LIB, sourced by EVERY full-gate entry point after tui/lib.sh (or info/ok/err defined):
# gates/push_gate.sh, gates/local_ci.sh (what every `gate.sh --full` execs), gates/round.sh's
# preflight, and the starter tools/gate.sh install.sh writes. The mechanism, the incident and the
# nesting rule are in lib/gate_slot.py's header; this is the shell's half:
#
#     gate_slot_acquire <repo name>   # blocks until a slot is free; announces the wait and its end
#     gate_slot_release               # the caller's cleanup calls it on every exit path
#     gate_slot_acquire_for_caller <repo name>
#         # structural.sh --full: when its PARENT is a `tools/gate.sh`, take the slot FOR that
#         # gate.sh, held until it ends (the consumer copies that predate the slot all call
#         # structural.sh first, and nothing else of goh's); otherwise take none
#
# GOH_GATE_SLOTS=N   how many gates at once (default: one per four cores, at least one;
#                    `python3 lib/gate_slot.py default` prints it). `0` turns the limit off.
# GOH_GATE_SLOT_DIR  the lock files (default ~/.cache/goh/slots): machine-wide on purpose.
# GOH_GATE_SLOT      set BY a gate holding a slot, "<slot file> <owner pid>", so a gate nested
#                    inside it takes none (never set it by hand: one naming a pid that is not an
#                    ancestor is ignored).
#
# The wait is reported apart from the gate's time: every step ceiling (GOH_LCI_TIMEOUT,
# GOH_STEP_TIMEOUT) belongs to a step the gate starts AFTER this returns, so a wait can never spend
# a step's budget. Pinned by tests/test_gate_slot.py.

GATE_SLOT_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gate_slot.py" # beside this file
GATE_SLOT_PID="" # the holder process (lib/gate_slot.py hold); gate_slot_release ends it

gate_slot_acquire() {
    _gate_slot_take "${1:-gate}" "$$" ""
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
    _gate_slot_take "${1:-gate}" "$caller" --for-caller
}

_gate_slot_take() { # <repo> <owner pid> <"" | --for-caller>
    local repo="$1" owner="$2" mode="$3" n="${GOH_GATE_SLOTS-}"
    local dir="${GOH_GATE_SLOT_DIR:-$HOME/.cache/goh/slots}"
    local helper="$GATE_SLOT_HELPER" fifo line idx total waited path pid mech
    case "$n" in
        "") ;;
        0) info "gate slots: off (GOH_GATE_SLOTS=0) -- this gate runs beside any number of others"
           return 0 ;;
        *[!0-9]*) err "GOH_GATE_SLOTS must be a non-negative integer of concurrent gates (got '$n')"
                  exit 2 ;;
    esac
    mkdir -p "$dir"
    if line="$(python3 -S "$helper" nested --dir "$dir")"; then
        info "gate slot: inside a gate that holds $line -- not taking another"
        return 0
    fi
    fifo="$(mktemp -u "${TMPDIR:-/tmp}/goh-gate-slot.XXXXXX")"
    mkfifo "$fifo"
    python3 -S "$helper" hold --dir "$dir" ${n:+--slots "$n"} --owner "$owner" --repo "$repo" \
        --handshake "$fifo" ${mode:+"$mode"} </dev/null &
    pid=$!
    # Read-write, so the open never blocks and a holder that died before writing cannot hang this
    # shell: the read times out, and the holder's liveness is asked between reads.
    exec 7<>"$fifo"
    while ! read -r -t 1 idx total waited path mech <&7; do
        if ! kill -0 "$pid" 2>/dev/null; then
            exec 7<&-
            rm -f "$fifo"
            err "gate slot: the slot holder (lib/gate_slot.py) exited without a slot"
            exit 1
        fi
    done
    exec 7<&-
    rm -f "$fifo"
    # WHICH watch took the slot: the kernel event (kqueue on macOS, a pidfd on Linux) or the pid
    # poll where the kernel has neither. Named, because a watch that cannot be seen cannot be
    # proved to have run -- the Linux pidfd branch shipped in v0.39.0 without ever being run.
    [ -n "$mech" ] || mech="poll"
    export GOH_GATE_SLOT="$path $owner"
    local whose="(GOH_GATE_SLOTS)"
    if [ -n "$mode" ]; then
        # Never this shell's to release: the holder ends with the gate.sh it was taken for.
        whose="for tools/gate.sh (pid $owner), held until it ends"
    else
        GATE_SLOT_PID="$pid"
    fi
    if [ "$waited" -gt 0 ]; then
        ok "gate slot $((idx + 1))/$total taken after ${waited}s waiting -- the step limits count from here"
        [ -z "$mode" ] || info "  $whose"
    else
        info "gate slot $((idx + 1))/$total taken $whose"
    fi
    info "  owner watch: $mech"
}

gate_slot_release() {
    if [ -n "$GATE_SLOT_PID" ]; then
        kill "$GATE_SLOT_PID" 2>/dev/null || true
        wait "$GATE_SLOT_PID" 2>/dev/null || true
    fi
    GATE_SLOT_PID=""
}
