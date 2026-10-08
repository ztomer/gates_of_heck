# shellcheck shell=bash
# _git_env.sh -- the variables that bind git to ONE repository (contract #12), handled in one place.
#
# git hands every hook GIT_DIR, and pre-commit GIT_INDEX_FILE too. From a LINKED worktree both are
# absolute, so anything the hook spawns that runs git on ANOTHER repository acts on this one: a
# consumer suite's scratch `git init` wrote core.bare=true into the shared config, its scratch
# commit landed on the committing branch and its `stash` on the shared stack (media_server,
# 2026-10-08); before that, zinc's pre-push (2026-09-27). Sourced by gates/_from_head.sh (so by
# every gate), gates/_goh_bin.sh, gates/_proven.sh and
# hooks/pre-commit. (scripts/build-goh.sh keeps its own inline unset: it must run from a tree with
# no gates/ at all, tests/test_goh_build_publish.py.)
#
#   goh_unbind_git        drop them from THIS shell. For one foreign call, in a subshell:
#                           ( goh_unbind_git; git -C "$other" ... )
#   goh_hook_unbind       at a hook's ENTRY, before anything is spawned: carry the index being
#                         committed when it is not the repository's own, then unbind. Returns 2,
#                         naming why, when the repository is reachable only through them.
#   goh_bind_hook_index   bind that carried index again, in THIS shell, for a reader of the index
#                         being committed -- and only inside the repository it was carried from.
#
# WHY THE INDEX IS CARRIED. A plain `git commit` hands the hook the repository's own index, which
# git finds again from the cwd; nothing is lost by dropping it. `commit -a` and `commit <paths>`
# commit a TEMPORARY index (index.lock, next-index-*.lock) that only GIT_INDEX_FILE names, and the
# staged scope must judge THAT tree (tests/test_commit_hook_git_env.py). So it travels as
# GOH_HOOK_INDEX_FILE, which git does not read, beside GOH_HOOK_GIT_DIR, the repository it belongs
# to: a gate a test runs on a fixture inherits both and binds nothing.

# The list is git's own (`--local-env-vars`, what it clears entering a submodule), never a copy:
# a hand-kept one had drifted to 7 of git's 15. Asked ONCE per process tree and exported -- it was
# ~1300 spawns per suite run (2026-10-06, tests/test_git_local_vars.py) -- and an inherited value is
# trusted only when it names GIT_DIR, the variable the list exists to drop.
case " ${GOH_GIT_LOCAL_VARS:-} " in
    *" GIT_DIR "*) ;;
    *) GOH_GIT_LOCAL_VARS="$(git rev-parse --local-env-vars 2>/dev/null | tr '\n' ' ')" ;;
esac
export GOH_GIT_LOCAL_VARS

goh_unbind_git() {
    # shellcheck disable=SC2086  # word-splitting git's variable list is the point
    unset ${GOH_GIT_LOCAL_VARS:-$(git rev-parse --local-env-vars 2>/dev/null)}
}

# _goh_git_phys <path> -- the physical spelling (/tmp vs /private/tmp), so two names of one file
# compare equal. A file is resolved through its directory: index.lock may be gone a moment later.
_goh_git_phys() {
    local dir
    dir="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" || { printf '%s\n' "$1"; return 0; }
    printf '%s/%s\n' "${dir%/}" "$(basename "$1")"
}

goh_hook_unbind() {
    local bound free own index
    # A carried index from an OUTER hook (a commit made inside a gate's test) is not this hook's.
    unset GOH_HOOK_INDEX_FILE GOH_HOOK_GIT_DIR
    if ! bound="$(git rev-parse --absolute-git-dir 2>/dev/null)"; then
        goh_unbind_git  # no repository even with them: nothing to carry, and nothing to keep
        return 0
    fi
    # What git finds from the cwd alone, and the index it would use there: one spawn for both.
    { read -r free; read -r own; } < <(goh_unbind_git
        git rev-parse --absolute-git-dir --path-format=absolute --git-path index 2>/dev/null)
    bound="$(_goh_git_phys "$bound")"
    if [ -z "${free:-}" ] || [ "$(_goh_git_phys "$free")" != "$bound" ]; then
        printf '%s\n' "✗ git hook: this repository ($bound) is reachable from $PWD only through" \
            "  GIT_DIR/GIT_WORK_TREE. Kept, every git a gate's child runs on another repository acts" \
            "  on this one; dropped, the gate cannot find it (contract #12). Nothing ran; this" \
            "  commit can only bypass the gate, with --no-verify." >&2
        return 2
    fi
    if [ -n "${GIT_INDEX_FILE:-}" ]; then
        index="$GIT_INDEX_FILE"
        case "$index" in /*) ;; *) index="$PWD/$index" ;; esac  # relative to the hook's cwd
        index="$(_goh_git_phys "$index")"
        if [ "$index" != "$(_goh_git_phys "${own:-}")" ]; then
            export GOH_HOOK_INDEX_FILE="$index" GOH_HOOK_GIT_DIR="$bound"
        fi
    fi
    goh_unbind_git
}

goh_bind_hook_index() {
    [ -n "${GOH_HOOK_INDEX_FILE:-}" ] && [ -n "${GOH_HOOK_GIT_DIR:-}" ] || return 0
    local here
    here="$(git rev-parse --absolute-git-dir 2>/dev/null)" || return 0
    [ "$(_goh_git_phys "$here")" = "$GOH_HOOK_GIT_DIR" ] || return 0
    export GIT_INDEX_FILE="$GOH_HOOK_INDEX_FILE"
}
