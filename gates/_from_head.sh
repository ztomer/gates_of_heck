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
# say nothing: so under THIS suite's pytest without GOH_LIVE this REFUSES, naming the fix.
#
# Not a checkout (an installed tarball), or the export cannot be built: the files here are run, with
# a line saying why -- the state before C4, never a silent change of which code judges.
#
#   executed:  . "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"
#   sourced:   . "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head_lib "${BASH_SOURCE[0]}" && return

# _goh_head_dir <file> -- sets _goh_copy to the export's copy of <file> (and exports GOH_DIR,
# GOH_LIVE_ROOT, PYTHONPATH for it); fails when <file> itself should run. Never in a $(...): the
# exports and the refusal's exit must reach the caller.
# The variables that bind git to ONE repository (contract #12): GOH_GIT_LOCAL_VARS, asked of git
# once per process tree and exported (checks/_gitutil.py reads it too), and the ONE shell helper
# that drops them, goh_unbind_git. This file is sourced first by every gate, so every gate has both.
# shellcheck source=gates/_git_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/_git_env.sh"

_goh_head_dir() {
    local here root rel head cache dest tmp
    _goh_copy=""
    _goh_run_root=""
    here="$(cd "$(dirname "$1")" && pwd -P)" || return 1
    # The checkout root: the nearest directory up from the script holding `.goh-head` (an export)
    # or `.git` (a checkout) -- tools/release-kit/ is two levels down, not one.
    root="$here"
    while [ "$root" != "/" ] && [ ! -f "$root/.goh-head" ] && [ ! -e "$root/.git" ]; do
        root="$(dirname "$root")"
    done
    _goh_run_root="$root"
    [ -n "${GOH_LIVE:-}" ] && return 1
    [ -f "$root/.goh-head" ] && return 1          # this IS an export: run it
    [ -e "$root/.git" ] || return 1               # not a checkout: nothing to export
    # THIS suite only (tests/conftest.py exports GATES_OF_HECK_SUITE). A consumer's own pytest that
    # runs a gate is a caller like any other and gets the export: keyed on PYTEST_CURRENT_TEST alone,
    # this refused 53 of ztools' 120 tests (2026-10-06), and GOH_LIVE there would run OUR live tree.
    if [ -n "${PYTEST_CURRENT_TEST:-}" ] && [ -n "${GATES_OF_HECK_SUITE:-}" ]; then
        echo "✗ gates_of_heck: a test ran $1 without GOH_LIVE=1 -- it would judge HEAD's export," \
            "not the tree under test. Keep GOH_LIVE in the test's environment." >&2
        exit 2
    fi
    head="$( (goh_unbind_git; git -C "$root" rev-parse -q --verify HEAD) )" \
        || return 1
    cache="${GOH_HEAD_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/goh/head}"
    dest="$cache/$head"
    if [ ! -f "$dest/.goh-head" ]; then
        mkdir -p "$cache" && tmp="$(mktemp -d "$cache/.build.XXXXXX")" || return 1
        if ! (goh_unbind_git; git -C "$root" archive "$head") \
                | tar -x -C "$tmp" 2>/dev/null; then
            rm -rf "$tmp"
            echo "⚠ gates_of_heck: could not export HEAD to $cache -- running the working tree at $root" >&2
            return 1
        fi
        printf '%s\n' "$head" > "$tmp/.goh-head"
        : > "$tmp/.goh-used"  # the last-use stamp, made before the rename (gates/_head_cache.py)
        # rename(2), whole: fails if another hook won the race, and theirs is the same commit.
        python3 -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$tmp" "$dest" 2>/dev/null \
            || rm -rf "$tmp"
        # A NEW export prunes the others by LAST USE (gates/_head_cache.py); the current never goes.
        python3 -S -c 'import sys; sys.path.insert(0, sys.argv[1]); import _head_cache; _head_cache.prune(sys.argv[2], sys.argv[3])' \
            "$(dirname "${BASH_SOURCE[0]}")" "$cache" "$head" 2>/dev/null || true
    fi
    # This use, stamped: a truncate of the export's own empty `.goh-used` -- no spawn, and the
    # export's files and directory are never rewritten.
    : > "$dest/.goh-used" 2>/dev/null || true
    rel="${here#"$root"}/$(basename "$1")"
    [ -f "$dest$rel" ] || return 1                # a file HEAD does not have yet: it is development
    export GOH_LIVE_ROOT="$root" GOH_DIR="$dest" PYTHONPATH="$dest${PYTHONPATH:+:$PYTHONPATH}"
    _goh_copy="$dest$rel"
}

goh_from_head() { # <this script> <args...> -- execs the export's copy, or returns to run this one
    if ! _goh_head_dir "$1"; then
        # THIS copy runs, so GOH_DIR names THIS tree. Unset, the native binary found its delegated
        # checkers at the default ~/Projects/gates_of_heck: a GOH_LIVE run from a worktree swept
        # the main checkout's checks/, and an edit there was never exercised (2026-10-06,
        # tests/test_gates_from_head.py). The export path already says so when it re-execs.
        [ -z "$_goh_run_root" ] || [ "$_goh_run_root" = "/" ] || export GOH_DIR="$_goh_run_root"
        return 0
    fi
    shift
    exec bash "$_goh_copy" "$@"
}

goh_from_head_lib() { # <this lib> -- sources the export's copy (true) or lets this one load (false)
    _goh_head_dir "$1" || return 1
    # shellcheck disable=SC1090
    . "$_goh_copy"
}
