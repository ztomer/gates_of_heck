# shellcheck shell=bash
# lib/landed_only.sh -- the commit half of GOH_LANDED_ONLY (GOH.LANDONLY, 2026-11-08).
#
# A repo that writes `GOH_LANDED_ONLY=main` in its .gatesrc moves that branch only through
# tools/land.sh, from a linked worktree. The PUSH was already refused for a tip land.sh had not
# gated (gates/push_gate.sh), but the rule against COMMITTING there lived in prose: SRV.NTFYROTATE
# committed 5c4936f straight onto gates_of_heck's main, and it reached origin inside the next
# landed tip. So the commit hook refuses it where it is made. Sourced by gates/commit_gate.sh:
#
#   goh_refuse_unlanded_commit <repo root> <what is refused>   # 0 allowed; 1 with the reason printed
#
# REFUSED: a commit in the PRIMARY checkout (its git dir IS the common git dir) while HEAD is the
# named branch. NOT refused: a linked worktree (its branch cannot be main: git forbids two
# checkouts of one branch), another branch, a detached HEAD, and every repo that names no branch.
# The branch is read from the repo's .gatesrc in a child -- never from the caller's environment,
# so an exported GOH_LANDED_ONLY neither turns the rule on in a consumer nor off here.
#
# THE TWO WAYS THROUGH, both named in the refusal:
#   GOH_LAND_CALLER=land.sh   set by tools/land.sh on the merge it makes, and by nothing else. (A
#                             fast-forward runs no pre-commit hook today; the token is what keeps
#                             that true if land.sh ever makes a commit of its own.)
#   GOH_MAIN_COMMIT_OK=<why>  the human escape; the reason is required, an empty one is no escape.
# Pinned by tests/test_landed_only_commit.py.
goh_refuse_unlanded_commit() {
    local root="$1" what="$2" landed branch gitdir common
    [ "${GOH_LAND_CALLER-}" = "land.sh" ] && return 0
    [ -f "$root/.gatesrc" ] || return 0
    landed="$(cd "$root" && bash -c 'unset GOH_LANDED_ONLY; set -a; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LANDED_ONLY-}"')" || return 0
    [ -n "$landed" ] || return 0
    branch="$(git -C "$root" symbolic-ref -q --short HEAD 2>/dev/null)" || return 0
    [ "$branch" = "$landed" ] || return 0
    gitdir="$(git -C "$root" rev-parse --absolute-git-dir 2>/dev/null)" || return 0
    common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 0
    [ "$(cd "$gitdir" && pwd -P)" = "$(cd "$common" && pwd -P)" ] || return 0   # a linked worktree
    [ -z "${GOH_MAIN_COMMIT_OK-}" ] || return 0
    printf '%s\n' "✗ $what is refused: $root is the primary checkout and HEAD is '$landed', which moves only through tools/land.sh (GOH_LANDED_ONLY)" \
        "  commit on a branch in a worktree (git worktree add ../wt -b my-branch), gate it and fast-forward main with:" \
        "    tools/land.sh                      # from that worktree" \
        "  a commit that cannot go through land.sh: GOH_MAIN_COMMIT_OK='<reason>' git commit ..." >&2
    return 1
}
