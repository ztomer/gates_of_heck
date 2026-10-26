# shellcheck shell=bash
# scripts/_github_repo.sh -- sourced by archive_repo.sh and unarchive_repo.sh: which GitHub repo a
# local clone IS, for the owner. Never run.
#
#   GOH_GH       the gh command (a test plants a fake; default `gh`)
#   GOH_GH_USER  the owner's GitHub login (default: `gh api user --jq .login`)
#
# goh_owner_repo <root>: prints `<remote>\t<owner>/<name>` for the ONE remote under the owner's
# account, or dies listing every remote and its owner -- a clone of someone else's repo (apes'
# `origin` is Galzai/apes; the owner's fork is `estate`) must never have the other account archived.
goh_owner_repo() {
    local root="$1" login remote url slug found="" listed=""
    login="${GOH_GH_USER:-$("${GOH_GH:-gh}" api user --jq .login)}" \
        || die "cannot ask GitHub who the owner is (gh api user) -- is gh signed in?"
    [ -n "$login" ] || die "GitHub returned no login for the owner"
    while read -r remote url; do
        slug="$(printf '%s\n' "$url" | sed -nE 's#^(https://|ssh://)?(git@)?github\.com[:/]([^/]+/[^/]+)$#\3#p')"
        slug="${slug%.git}"
        listed="$listed $remote=${slug:-$url}"
        [ -n "$slug" ] || continue
        if [ "$(printf '%s' "${slug%%/*}" | tr '[:upper:]' '[:lower:]')" = \
             "$(printf '%s' "$login" | tr '[:upper:]' '[:lower:]')" ]; then
            [ -z "$found" ] || [ "${found#*	}" = "$slug" ] \
                || die "two remotes under $login: ${found#*	} and $slug -- cannot tell which is this repo"
            found="$remote	$slug"
        fi
    done < <(git -C "$root" remote -v | awk '$3 == "(push)" { print $1, $2 }')
    [ -n "$found" ] || die "no remote of $root is under $login's account (remotes:${listed:- none}) -- refusing to touch another account's repo"
    printf '%s\n' "$found"
}

# goh_gh_archived <owner/name>: prints true or false.
goh_gh_archived() {
    "${GOH_GH:-gh}" repo view "$1" --json isArchived --jq .isArchived
}
