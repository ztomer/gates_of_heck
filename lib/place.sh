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
# whose pid still runs is mid-copy and is left alone -- and "its pid" is the writer, not whatever
# process the number went to after a killed copy: the stage is written by its process, so its mtime
# bounds the writer's start, and a process at the pid that started after it is a stranger
# (GOH.STRAYSIGNAL3, lib/pid_owner.sh goh_pid_alive_since). tests/test_place_staging.py.
#
# Since GOH.CLASS.CHECKOUT part 2 the stage is not beside its destination at all: it is
# `<base>.<pid>.staging` in the destination directory's own run directory (lib/run_path.sh), outside
# every work tree, so a killed copy leaves nothing even `git status --ignored` shows. rename(2) is
# atomic only within one filesystem, so when the run root is on another device than the destination
# the stage falls back to `<dest>.<pid>.staging` beside it (ignored, reaped) rather than let `mv`
# degrade to a copy a reader could see half of. Both homes are reaped.

# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/place.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/run_path.sh
. "${BASH_SOURCE[0]%/*}/run_path.sh" || exit 1
# shellcheck source=lib/pid_owner.sh
. "${BASH_SOURCE[0]%/*}/pid_owner.sh" || exit 1

# goh_place <src> <dest>
goh_place() {
    local stage
    mkdir -p "$(dirname "$2")" || return 1
    goh_place_reap "$2"
    stage="$(_goh_place_stage "$2")" || return 1
    if cp "$1" "$stage" && mv "$stage" "$2"; then
        return 0
    fi
    rm -f "$stage"
    return 1
}

# _goh_place_dev <path> -- the device number of PATH: GNU stat first, since GNU `stat -f %d` exits
# 0 printing a filesystem's free inodes; BSD stat refuses `-c` and answers `-f %d`.
_goh_place_dev() {
    stat -c %d "$1" 2>/dev/null || stat -f %d "$1"
}

# _goh_place_stage <dest> -- where DEST's stage goes: its directory's run directory, unless that is
# on another filesystem, where only the stage beside DEST renames atomically.
_goh_place_stage() {
    local dir run base="${1##*/}" pid="${BASHPID:-$$}"
    dir="$(dirname "$1")"
    if run="$(goh_run_dir "$dir" 2>/dev/null)" \
        && [ "$(_goh_place_dev "$run")" = "$(_goh_place_dev "$dir")" ]; then
        printf '%s\n' "$run/$base.$pid.staging"
    else
        printf '%s\n' "$1.$pid.staging"  # run-path-ok: another filesystem; rename(2) is atomic only within one
    fi
}

# goh_place_reap <dest> -- removes DEST's stages whose process no longer runs, in both homes.
goh_place_reap() {
    local f pid run base="${1##*/}"
    run="$(goh_run_dir "$(dirname "$1")" 2>/dev/null)" || run=""
    for f in "$1".*.staging ${run:+"$run/$base".*.staging}; do
        [ -f "$f" ] || continue
        pid="${f%.staging}"
        pid="${pid##*.}"
        case "$pid" in "" | *[!0-9]*) continue ;; esac
        goh_pid_alive_since "$pid" "$f" && continue
        rm -f "$f"
    done
}
