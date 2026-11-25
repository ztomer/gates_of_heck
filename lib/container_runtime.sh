# shellcheck shell=bash
# container_runtime.sh -- shared lifecycle for container runtimes (colima / docker).
#
# HOUSE LIB, sourced. Ensures docker is answering before tests or build steps run.
# If docker is down, colima is started and reference-counted leases track active gates.
# When the last lease releases, colima is stopped if and only if goh started it
# and no other container is running on it (owner ruling OW.27).
#
# Usage:
#   . "$GOH_DIR/lib/container_runtime.sh"
#   trap 'goh_container_runtime_down' EXIT
#   goh_container_runtime_up "my-label" || exit 1
#
# Sourced by consumers (`goh_container_runtime_up`, `goh_container_runtime_down`): starts colima if
# docker is down, takes reference-counted leases, and stops colima when the last lease releases if
# goh started it.

# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/container_runtime.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
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
# shellcheck source=lib/lock_order.sh
. "${BASH_SOURCE[0]%/*}/lock_order.sh" || return 1   # the one lock order (GOH.LOCKORDER)
# shellcheck source=lib/pid_owner.sh
. "${BASH_SOURCE[0]%/*}/pid_owner.sh" || return 1   # a lease's holder judged by its start time

_goh_container_lock_acquire() {
    local state_dir="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"
    mkdir -p "$state_dir" </dev/null
    exec 8>>"$state_dir/lock"
    goh_lock_take container 8
}

_goh_container_lock_release() {
    exec 8>&-
    goh_lock_drop container
}

_goh_container_live_leases() {
    local state_dir="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"
    local leases_dir="$state_dir/leases"
    local count=0
    if [ -d "$leases_dir" ]; then
        local lease pid stamp
        for lease in "$leases_dir"/*; do
            [ -f "$lease" ] || continue
            pid="${lease##*/}"
            case "$pid" in
                *[!0-9]*) rm -f "$lease" 2>/dev/null || true; continue ;;
            esac
            # The lease's second line is its holder's stamp (GOH.STRAYSIGNAL2): a dead gate's pid
            # handed to another process is no live lease. A lease without one (an older goh's) is
            # judged by its pid as it stands now.
            stamp="$(sed -n 2p "$lease" </dev/null 2>/dev/null)"
            [ -n "$stamp" ] || stamp="$(goh_pid_stamp "$pid" </dev/null)" || stamp=""
            if goh_pid_alive "$stamp"; then
                count=$((count + 1))
            else
                rm -f "$lease" 2>/dev/null || true
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
            rm -f "$state_dir/started_by_goh" 2>/dev/null || true
        fi
    else
        if ! command -v "$colima" </dev/null >/dev/null 2>&1; then
            _goh_container_lock_release
            err "container runtime: docker is down and no colima to start ($colima)"
            return 1
        fi

        local start_log="$state_dir/colima_start.log"
        info "starting container runtime ($colima start)"
        # `colima start` leaves daemons behind (`colima daemon`, `limactl hostagent`)
        # that keep every fd they are handed for the VM's whole life. Handed fd 8,
        # they held this lock forever, and the same process's own down blocked on
        # it until its gate was killed (2026-11-01); handed a caller's tree_lock
        # fd 9, they would pin the tree. So the start gets neither.
        if ! "$colima" start </dev/null >"$start_log" 2>&1 8>&- 9>&- || ! "$docker" info </dev/null >/dev/null 2>&1; then
            _goh_container_lock_release
            err "container runtime: colima start failed (log: $start_log)"
            return 1
        fi
        : > "$state_dir/started_by_goh"
    fi

    mkdir -p "$leases_dir" </dev/null 2>/dev/null || true
    printf '%s\n%s\n' "$label" "$(goh_pid_stamp $$)" > "$leases_dir/$$"

    _goh_container_lock_release
    return 0
}

goh_container_runtime_down() {
    local colima="${COLIMA:-colima}"
    local state_dir="${GOH_CONTAINER_STATE:-$HOME/.cache/goh/container_runtime}"
    local leases_dir="$state_dir/leases"

    # No lease of this process's own: up never ran or failed, and a down it
    # never earned touches nothing (consumers trap down before calling up).
    [ -f "$leases_dir/$$" ] || return 0

    _goh_container_lock_acquire

    rm -f "$leases_dir/$$" 2>/dev/null || true

    local live
    live="$(_goh_container_live_leases)"

    if [ "$live" -eq 0 ] && [ -f "$state_dir/started_by_goh" ]; then
        # Owner ruling OW.27: never under anyone else's running container.
        if [ -n "$("${DOCKER:-docker}" ps -q </dev/null 2>/dev/null)" ]; then
            warn "container runtime: leaving $colima running: other containers are on it"
            rm -f "$state_dir/started_by_goh" 2>/dev/null || true
            _goh_container_lock_release
            return 0
        fi
        info "stopping container runtime ($colima stop)"
        "$colima" stop </dev/null >/dev/null 2>&1 || true
        rm -f "$state_dir/started_by_goh" 2>/dev/null || true
    fi

    _goh_container_lock_release
    return 0
}
