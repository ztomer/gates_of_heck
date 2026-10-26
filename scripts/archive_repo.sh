#!/usr/bin/env bash
# archive_repo.sh <repo> [--reason "<why>"] -- archive the repo on GitHub and keep the local clone
# ON HOLD and read-only (scripts/hold_repo.sh), so nothing works on it until it is unarchived.
#
# Refuses, changing nothing, unless the clone has nothing that exists only here: a clean tree (no
# modified or untracked file), no stash, and every local branch's commits on the owner's remote
# (judged from the remote-tracking refs; fetch first if they may be stale). The remote archived is
# the ONE under the owner's account (scripts/_github_repo.sh) -- never an upstream someone else owns.
# Then: `gh repo archive` (skipped when GitHub already says archived), the hold with
# unarchive_repo.sh as its lift, and `chmod -R a-w`. Seams: GOH_GH, GOH_GH_USER.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=scripts/_github_repo.sh
. "$GOH/scripts/_github_repo.sh"

usage() { die "usage: archive_repo.sh <repo> [--reason \"<why>\"]" 2; }
repo="" why=""
while [ $# -gt 0 ]; do
    case "$1" in
        --reason) [ $# -ge 2 ] || usage; why="$2"; shift 2 ;;
        -*) usage ;;
        *) [ -z "$repo" ] || usage; repo="$1"; shift ;;
    esac
done
[ -n "$repo" ] || usage
root="$(cd "$repo" && git rev-parse --show-toplevel 2>/dev/null)" \
    || die "archive_repo.sh: $repo is not a git repository"

# Nothing may exist only here.
dirty="$(git -C "$root" status --porcelain --untracked-files=all)"
if [ -n "$dirty" ]; then
    err "archive_repo.sh: $root has changes no remote holds -- commit and push them, or remove them:"
    printf '%s\n' "$dirty" | sed -n 1,20p | while IFS= read -r line; do step "$line"; done >&2
    exit 1
fi
stashes="$(git -C "$root" stash list)"
[ -z "$stashes" ] || die "archive_repo.sh: $root has stash entries (git stash list) -- they exist only here"

pick="$(goh_owner_repo "$root")"
remote="${pick%%	*}" slug="${pick#*	}"
unpushed="$(git -C "$root" log --branches --not --remotes="$remote" --format='%h %s' --)"
if [ -n "$unpushed" ]; then
    err "archive_repo.sh: commits on local branches that $remote ($slug) does not have -- push them first:"
    printf '%s\n' "$unpushed" | sed -n 1,20p | while IFS= read -r line; do step "$line"; done >&2
    exit 1
fi

if [ "$(goh_gh_archived "$slug")" = "true" ]; then
    info "$slug is already archived on GitHub"
else
    "${GOH_GH:-gh}" repo archive "$slug" --yes
    # The state, not the exit code (G15): GitHub is asked again before anything is claimed or held.
    [ "$(goh_gh_archived "$slug")" = "true" ] \
        || die "archive_repo.sh: gh repo archive returned, but $slug is still not archived on GitHub -- nothing held"
    ok "archived $slug on GitHub"
fi
reason="archived on GitHub as $slug${why:+: $why}; unarchive it there first"
bash "$GOH/scripts/hold_repo.sh" "$root" --reason "$reason" --read-only \
    --lift "\"$GOH/scripts/unarchive_repo.sh\" $root"
exit
} # parse-guard
