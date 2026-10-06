#!/usr/bin/env bash
# round.sh -m "<message>" <path>... -- one round, end to end: commit the NAMED paths, push the
# commit, and prove the remote has it. Ported from ZoneWM's tools/round.sh (2026-10-06), where a
# hand-written chain failed three ways in one night: a commit made after a red verify (`;` where
# `&&` belonged), a commit that never happened (`git add` named a path a `git mv` had moved, and
# the `rc` printed was another command's), and pushes refused for a full disk that read as a
# codesign error. Each step here stops the round with its own reason.
#
# The gates are the repo's own hooks: the commit runs pre-commit, the push runs pre-push. So a round
# is never weaker than a hand-run one, and needs no copy of either.
#   * only the named paths are committed (`git commit --only`): anything else already staged stays
#     staged and out of the commit -- the hole ZoneWM's version named;
#   * GOH_ROUND_NEVER (default `.claude/settings.local.json`) is never committed;
#   * named `.py` files are ruff-formatted first when the repo opts into GOH_PYTHON_FORMATTED;
#   * GOH_MIN_FREE_GIB is checked before the commit (lib/preflight_disk.py);
#   * the push is PINNED (`<sha>:refs/heads/<branch>`), and the remote is read back afterwards.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../tui/lib.sh"

msg=""
while [ $# -gt 0 ]; do
    case "$1" in
        -m) [ $# -ge 2 ] || die "round refused: -m needs a message"; msg="$2"; shift 2 ;;
        --) shift; break ;;
        -*) die "round refused: '$1' is not a path (usage: round.sh -m <message> <path>...)" ;;
        *) break ;;
    esac
done
[ -n "$msg" ] || die "round refused: no commit message (-m)"
[ "$#" -gt 0 ] || die "round refused: no paths -- a round commits named paths only, never everything"

root="$(git rev-parse --show-toplevel)" || die "round refused: not inside a git repo"
cd "$root"
setting() { # <key> -- its value in .gatesrc, read by name in a child (push_gate.sh's rule)
    [ -f .gatesrc ] || return 0
    bash -c 'set -a; . ./.gatesrc; eval "printf %s \"\${$1-}\""' _ "$1"
}
never="$(setting GOH_ROUND_NEVER)"; never="${never:-.claude/settings.local.json}"
for p in "$@"; do
    case "$p" in -*) die "round refused: '$p' is not a path" ;; esac
    for n in $never; do [ "$p" != "$n" ] || die "round refused: $n is never committed (GOH_ROUND_NEVER)"; done
    [ -e "$p" ] || git ls-files --error-unmatch -- "$p" >/dev/null 2>&1 \
        || die "round refused: '$p' is neither on disk nor tracked"
done

need="$(setting GOH_MIN_FREE_GIB)"
if [ -n "$need" ]; then
    python3 -S "$HERE/../lib/preflight_disk.py" . "$need" || die "round stopped: free space first"
fi

if [ -n "$(setting GOH_PYTHON_FORMATTED)" ]; then
    py=()
    for p in "$@"; do case "$p" in *.py) [ -e "$p" ] && py+=("$p") ;; esac; done
    if [ "${#py[@]}" -gt 0 ]; then
        ruff format -q -- "${py[@]}" || die "round stopped: ruff format failed"
        ok "${#py[@]} Python file(s) formatted"
    fi
fi

section "commit (the pre-commit hook gates it)"
git add -- "$@"
git diff --cached --quiet -- "$@" && die "round stopped: nothing to commit in the named paths"
git commit -q --only -F - -- "$@" <<<"$msg" || die "round stopped: the commit was refused (above)"
sha="$(git rev-parse HEAD)"
ok "$(git log --oneline -1)"

section "push (the pre-push hook gates it)"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" \
    || die "round stopped: no upstream for $(git branch --show-current) -- the commit is local"
remote="${upstream%%/*}" branch="${upstream#*/}"
git push -q "$remote" "$sha:refs/heads/$branch" \
    || die "round stopped: the push was refused (above) -- the commit is local"
have="$(git ls-remote "$remote" "refs/heads/$branch" | cut -f1)"
[ "$have" = "$sha" ] || die "round stopped: $remote/$branch is ${have:-missing}, not ${sha:0:12}, after a push that returned 0"
git fetch -q "$remote" "$branch" 2>/dev/null || true
ok "$remote/$branch has ${sha:0:12}"
exit
} # parse-guard
