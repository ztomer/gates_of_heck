# shellcheck shell=bash
# gate_slot.sh -- a machine-wide limit on how many gates run their heavy steps at once.
#
# HOUSE LIB, sourced by gates/push_gate.sh after tui/lib.sh. The mechanism, the incident and the
# nesting rule are in lib/gate_slot.py's header; this is the shell's half:
#
#     gate_slot_acquire <repo name>   # blocks until a slot is free; announces the wait and its end
#     gate_slot_release               # the caller's cleanup calls it on every exit path
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

GATE_SLOT_PID="" # the holder process (lib/gate_slot.py hold); gate_slot_release ends it

gate_slot_acquire() {
    local repo="${1:-gate}" n="${GOH_GATE_SLOTS-}" dir="${GOH_GATE_SLOT_DIR:-$HOME/.cache/goh/slots}"
    local helper="$GOH/lib/gate_slot.py" fifo line idx total waited path
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
    python3 -S "$helper" hold --dir "$dir" ${n:+--slots "$n"} --owner "$$" --repo "$repo" \
        --handshake "$fifo" </dev/null &
    GATE_SLOT_PID=$!
    # Read-write, so the open never blocks and a holder that died before writing cannot hang this
    # shell: the read times out, and the holder's liveness is asked between reads.
    exec 7<>"$fifo"
    while ! read -r -t 1 idx total waited path <&7; do
        if ! kill -0 "$GATE_SLOT_PID" 2>/dev/null; then
            exec 7<&-
            rm -f "$fifo"
            GATE_SLOT_PID=""
            err "gate slot: the slot holder (lib/gate_slot.py) exited without a slot"
            exit 1
        fi
    done
    exec 7<&-
    rm -f "$fifo"
    export GOH_GATE_SLOT="$path $$"
    if [ "$waited" -gt 0 ]; then
        ok "gate slot $((idx + 1))/$total taken after ${waited}s waiting -- the step limits count from here"
    else
        info "gate slot $((idx + 1))/$total taken (GOH_GATE_SLOTS)"
    fi
}

gate_slot_release() {
    if [ -n "$GATE_SLOT_PID" ]; then
        kill "$GATE_SLOT_PID" 2>/dev/null || true
        wait "$GATE_SLOT_PID" 2>/dev/null || true
    fi
    GATE_SLOT_PID=""
}
