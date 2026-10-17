# shellcheck shell=bash
# The push gate's run ownership: who holds a run dir under `$export_root`, and reaping what a killed
# gate left behind. Sourced by gates/push_gate.sh after the tui lib (for `info`).

# `<pid> <start time>`: the identity of a live process. A pid alone is reused; the pair is not.
owner_stamp() {
    printf '%s %s\n' "$1" "$(ps -o lstart= -p "$1" 2>/dev/null | tr -s ' ' | sed 's/^ //;s/ $//')"
}

# Reap what killed runs left under export root $1: a run directory whose owner is not alive. Only directories carrying an
# `.owner` are judged; anything else in the root is not this layout's to delete.
reap_dead_runs() {
    local export_root="$1" dir pid
    for dir in "$export_root"/*/; do
        [ -f "$dir.owner" ] || continue
        read -r pid _ <"$dir.owner" || continue
        if [ "$(owner_stamp "$pid")" = "$(cat "$dir.owner")" ] && kill -0 "$pid" 2>/dev/null; then
            continue
        fi
        info "pre-push: removing a run left by a killed gate: ${dir%/}"
        rm -rf "$dir"
    done
    # A stable export's `.source` marker outlives its runs by design, and dies with its checkout.
    local marker owner
    for marker in "$export_root"/*.source; do
        [ -f "$marker" ] || continue
        read -r owner <"$marker" || owner=""
        [ -n "$owner" ] && [ -e "$owner" ] || rm -f "$marker"
    done
}
