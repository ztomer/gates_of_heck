#!/usr/bin/env bash
# unarchive_repo.sh <repo> -- lift an archive's hold, once GitHub says the repo is live again.
#
# The OWNER unarchives on GitHub; this checks that it happened (`gh repo view --json isArchived` on
# the owner's remote) and only then releases the hold (scripts/release_hold.sh: `chmod -R u+w`, then
# the hold keys removed). Run interactively, it offers `gh repo unarchive` itself; run by anything
# else (an agent, a pipe), it refuses and names the command. Seams: GOH_GH, GOH_GH_USER, and
# GOH_HOLD_INTERACTIVE=1 to answer the offer from stdin without a terminal.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=scripts/_github_repo.sh
. "$GOH/scripts/_github_repo.sh"

[ $# -eq 1 ] && [ "${1#-}" = "$1" ] || die "usage: unarchive_repo.sh <repo>" 2
root="$(cd "$1" && git rev-parse --show-toplevel 2>/dev/null)" \
    || die "unarchive_repo.sh: $1 is not a git repository"

pick="$(goh_owner_repo "$root")"
slug="${pick#*	}"
if [ "$(goh_gh_archived "$slug")" = "true" ]; then
    if [ -t 0 ] || [ -n "${GOH_HOLD_INTERACTIVE:-}" ]; then
        printf '%s' "$slug is still archived on GitHub. Unarchive it there now? [y/N] "
        answer=""
        read -r answer || true
        case "$answer" in
            y|Y|yes)
                "${GOH_GH:-gh}" repo unarchive "$slug" --yes
                [ "$(goh_gh_archived "$slug")" = "false" ] \
                    || die "unarchive_repo.sh: gh repo unarchive returned, but $slug is still archived on GitHub -- the hold stays"
                ok "unarchived $slug on GitHub" ;;
            *) die "unarchive_repo.sh: $slug stays archived, and so does $root" ;;
        esac
    else
        die "unarchive_repo.sh: $slug is still archived on GitHub -- the owner unarchives it (gh repo unarchive $slug), then re-run this"
    fi
fi
GOH_HOLD_LIFTED_BY=unarchive_repo.sh bash "$GOH/scripts/release_hold.sh" "$root"
exit
} # parse-guard
