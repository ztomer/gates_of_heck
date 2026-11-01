# shellcheck shell=bash
# container_runtime.sh -- shared lifecycle for container runtimes (colima / docker).
#
# HOUSE LIB, sourced. Ensures docker is answering before tests or build steps run.
# If docker is down, colima is started and reference-counted leases track active gates.
# When the last lease releases, colima is stopped if and only if goh started it.
#
# Usage:
#   . "$GOH_DIR/lib/container_runtime.sh"
#   trap 'goh_container_runtime_down' EXIT
#   goh_container_runtime_up "my-label" || exit 1

if [ -n "${GOH_DIR:-}" ] && [ -f "$GOH_DIR/tui/lib.sh" ]; then
    # shellcheck source=tui/lib.sh
    . "$GOH_DIR/tui/lib.sh"
elif [ -f "${BASH_SOURCE[0]%/*}/../tui/lib.sh" ]; then
    # shellcheck source=tui/lib.sh
    . "${BASH_SOURCE[0]%/*}/../tui/lib.sh"
fi

DOCKER="${DOCKER:-docker}"
COLIMA="${COLIMA:-colima}"
GOH_CONTAINER_STATE="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"

# The lock is tree_lock.sh's idiom (a kernel flock the shell holds on an fd,
# released on ANY exit, SIGKILL included, so there is no stale state to judge),
# on fd 8: a consumer may already hold tree_lock's fd 9 in the same shell.
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/house_python.sh" || return 1

_goh_container_lock_acquire() {
    local state_dir="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"
    mkdir -p "$state_dir" </dev/null
    exec 8>>"$state_dir/lock"
    "$GOH_PY" -c 'import fcntl; fcntl.flock(8, fcntl.LOCK_EX)' </dev/null
}

_goh_container_lock_release() {
    exec 8>&-
}

_goh_container_live_leases() {
    local state_dir="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"
    local leases_dir="$state_dir/leases"
    local count=0
    if [ -d "$leases_dir" ]; then
        local lease pid
        for lease in "$leases_dir"/*; do
            [ -f "$lease" ] || continue
            pid="${lease##*/}"
            case "$pid" in
                *[!0-9]*) rm -f "$lease" </dev/null 2>/dev/null || true; continue ;;
            esac
            if kill -0 "$pid" </dev/null 2>/dev/null; then
                count=$((count + 1))
            else
                rm -f "$lease" </dev/null 2>/dev/null || true
            fi
        done
    fi
    printf '%d' "$count"
}

goh_container_runtime_up() {
    local label="${1:?goh_container_runtime_up requires a label}"
    local docker="${DOCKER:-docker}"
    local colima="${COLIMA:-colima}"
    local state_dir="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"
    local leases_dir="$state_dir/leases"

    if ! command -v "$docker" </dev/null >/dev/null 2>&1; then
        err "container runtime: no docker CLI ($docker)"
        return 1
    fi

    _goh_container_lock_acquire

    local live
    live="$(_goh_container_live_leases)"

    if "$docker" info </dev/null >/dev/null 2>&1; then
        # Docker answers and no live lease holds it: whoever started it, goh
        # cannot prove it was goh (a marker left by a killed gate is not proof),
        # so it is never goh's to stop.
        if [ "$live" -eq 0 ]; then
            rm -f "$state_dir/started_by_goh" </dev/null 2>/dev/null || true
        fi
    else
        if ! command -v "$colima" </dev/null >/dev/null 2>&1; then
            _goh_container_lock_release
            err "container runtime: docker is down and no colima to start ($colima)"
            return 1
        fi

        local start_log="$state_dir/colima_start.log"
        info "starting container runtime ($colima start)"
        if ! "$colima" start </dev/null >"$start_log" 2>&1 || ! "$docker" info </dev/null >/dev/null 2>&1; then
            _goh_container_lock_release
            err "container runtime: colima start failed (log: $start_log)"
            return 1
        fi
        : > "$state_dir/started_by_goh"
    fi

    mkdir -p "$leases_dir" </dev/null 2>/dev/null || true
    printf '%s\n' "$label" > "$leases_dir/$$"

    _goh_container_lock_release
    return 0
}

goh_container_runtime_down() {
    local colima="${COLIMA:-colima}"
    local state_dir="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"
    local leases_dir="$state_dir/leases"

    _goh_container_lock_acquire

    rm -f "$leases_dir/$$" </dev/null 2>/dev/null || true

    local live
    live="$(_goh_container_live_leases)"

    if [ "$live" -eq 0 ] && [ -f "$state_dir/started_by_goh" ]; then
        info "stopping container runtime ($colima stop)"
        "$colima" stop </dev/null >/dev/null 2>&1 || true
        rm -f "$state_dir/started_by_goh" </dev/null 2>/dev/null || true
    fi

    _goh_container_lock_release
    return 0
}
