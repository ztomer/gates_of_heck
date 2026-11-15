# shellcheck shell=bash
# place.sh -- sourced, never run. The ONE way a binary is put in place: copied whole to a stage
# beside its destination, then renamed, so a reader (or a hook executing the old one) never sees
# half a binary (GOH.STAGINGSTRAY).
#
# 2026-11-14 21:57 a placement of bin/goh was killed mid-`cp`: `bin/goh.10644.staging`, 2 MiB of a
# 9 MB binary, stayed in the gates_of_heck checkout as an untracked file and the checkout read
# "1 uncommitted". So the stage is `<dest>.<pid>.staging` -- a name the checkout's .gitignore
# ignores (`/bin/*.staging`) -- a copy that fails removes its stage, and every placement first
# reaps the stages beside its destination whose process is gone (SIGKILL runs no trap). A stage
# whose pid still runs is mid-copy and is left alone. tests/test_place_staging.py.

. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"

# goh_place <src> <dest>
goh_place() {
    local stage="$2.${BASHPID:-$$}.staging"
    mkdir -p "$(dirname "$2")" || return 1
    goh_place_reap "$2"
    if cp "$1" "$stage" && mv "$stage" "$2"; then
        return 0
    fi
    rm -f "$stage"
    return 1
}

# goh_place_reap <dest> -- removes `<dest>.<pid>.staging` files whose process no longer runs.
goh_place_reap() {
    local f pid
    for f in "$1".*.staging; do
        [ -f "$f" ] || continue
        pid="${f%.staging}"
        pid="${pid##*.}"
        case "$pid" in "" | *[!0-9]*) continue ;; esac
        kill -0 "$pid" 2>/dev/null && continue
        rm -f "$f"
    done
}
