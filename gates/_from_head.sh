# shellcheck shell=bash
# _from_head.sh — sourced FIRST by every gates entry point. Consumers run HEAD, not the tree (C4).
#
# Gate source edited in the shared checkout was LIVE in every consumer the moment it was saved:
# on 2026-10-05 alone an uncommitted `_goh_bin.sh` reached antiknob's push ahead of the binary it
# checks, a calibration that disabled a branch "for a few seconds" turned another session's suite
# red, and media_server's pre-push warned on a peer's half-done `_line_cap.sh`. A warning names
# that; this removes it. Every entry point re-runs itself from an EXPORT of HEAD:
#
#   ~/.cache/goh/head/<HEAD sha>/   (GOH_HEAD_CACHE)  built once per commit by `git archive`,
#                                    renamed into place whole, and never written again
#
# so a gate can neither read uncommitted source nor a half-updated export. bin/goh is not in git:
# the export's resolver finds it in the live checkout (GOH_LIVE_ROOT), where C3 keeps it HEAD's.
# PYTHONPATH gains the export first, so `import tui.lib` stops reaching the live tree too
# (~/.zshrc exports PYTHONPATH at the checkout).
#
# GOH_LIVE=1 runs the working tree ON PURPOSE -- developing the gates, and this repo's own suite
# (tests/conftest.py sets it). A test that drops it would test HEAD, not the change under test, and
# say nothing: so under pytest without GOH_LIVE this REFUSES, naming the fix.
#
# Not a checkout (an installed tarball), or the export cannot be built: the files here are run, with
# a line saying why -- the state before C4, never a silent change of which code judges.
#
#   executed:  . "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"
#   sourced:   . "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head_lib "${BASH_SOURCE[0]}" && return

# _goh_head_dir <file> -- sets _goh_copy to the export's copy of <file> (and exports GOH_DIR,
# GOH_LIVE_ROOT, PYTHONPATH for it); fails when <file> itself should run. Never in a $(...): the
# exports and the refusal's exit must reach the caller.
_goh_head_dir() {
    local here root rel head cache dest tmp
    _goh_copy=""
    [ -n "${GOH_LIVE:-}" ] && return 1
    here="$(cd "$(dirname "$1")" && pwd -P)" || return 1
    root="$(cd "$here/.." && pwd -P)" || return 1
    [ -f "$root/.goh-head" ] && return 1          # this IS an export: run it
    [ -e "$root/.git" ] || return 1               # not a checkout: nothing to export
    if [ -n "${PYTEST_CURRENT_TEST:-}" ]; then
        echo "✗ gates_of_heck: a test ran $1 without GOH_LIVE=1 -- it would judge HEAD's export," \
            "not the tree under test. Keep GOH_LIVE in the test's environment." >&2
        exit 2
    fi
    # shellcheck disable=SC2046  # word-splitting git's variable list is the point
    head="$( (unset $(git rev-parse --local-env-vars 2>/dev/null); git -C "$root" rev-parse -q --verify HEAD) )" \
        || return 1
    cache="${GOH_HEAD_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/goh/head}"
    dest="$cache/$head"
    if [ ! -f "$dest/.goh-head" ]; then
        mkdir -p "$cache" && tmp="$(mktemp -d "$cache/.build.XXXXXX")" || return 1
        # shellcheck disable=SC2046
        if ! (unset $(git rev-parse --local-env-vars 2>/dev/null); git -C "$root" archive "$head") \
                | tar -x -C "$tmp" 2>/dev/null; then
            rm -rf "$tmp"
            echo "⚠ gates_of_heck: could not export HEAD to $cache -- running the working tree at $root" >&2
            return 1
        fi
        printf '%s\n' "$head" > "$tmp/.goh-head"
        # rename(2), whole: fails if another hook won the race, and theirs is the same commit.
        python3 -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$tmp" "$dest" 2>/dev/null \
            || rm -rf "$tmp"
        # Exports of other commits, untouched for a week, go; the current one never does.
        find "$cache" -mindepth 1 -maxdepth 1 -type d -mtime +7 ! -name "$head" -exec rm -rf {} + 2>/dev/null || true
    fi
    rel="${here#"$root"}/$(basename "$1")"
    [ -f "$dest$rel" ] || return 1                # a file HEAD does not have yet: it is development
    export GOH_LIVE_ROOT="$root" GOH_DIR="$dest" PYTHONPATH="$dest${PYTHONPATH:+:$PYTHONPATH}"
    _goh_copy="$dest$rel"
}

goh_from_head() { # <this script> <args...> -- execs the export's copy, or returns to run this one
    _goh_head_dir "$1" || return 0
    shift
    exec bash "$_goh_copy" "$@"
}

goh_from_head_lib() { # <this lib> -- sources the export's copy (true) or lets this one load (false)
    _goh_head_dir "$1" || return 1
    # shellcheck disable=SC1090
    . "$_goh_copy"
}
