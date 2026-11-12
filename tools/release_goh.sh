#!/usr/bin/env bash
# release_goh.sh VERSION -- gates_of_heck's own release kit, `goh ship`'s GOH_SHIP_RELEASE (GOH.SHIPKIT).
#
# goh ship could not release goh: no kit was declared, and v0.95.0's bump (8e90921) was made by
# hand. This does what that commit did, the house way:
#
#   1. refuse, before anything moves, a dirty tree, a VERSION that is not X.Y.Z, a tag that already
#      exists, and an Unreleased with nothing to release (changelog_cut.py ready);
#   2. in a build/ worktree off HEAD: [workspace.package] version and Cargo.lock's workspace
#      entries set to VERSION, Unreleased cut into `## vVERSION` (titled with GOH_SHIP_SUMMARY when
#      ship gives one), one `release: vVERSION -- TITLE` commit through the hooks;
#   3. tools/land.sh from that worktree: main moves only through land (.gatesrc GOH_LANDED_ONLY),
#      never a commit in the main checkout and never GOH_MAIN_COMMIT_OK;
#   4. push main, then an annotated tag vVERSION whose message is the stanza, then push the tag.
#
# It pushes, so .gatesrc says GOH_SHIP_RELEASE_PUSHES=1. Pinned by tests/test_release_goh.py.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=lib/house_python.sh
. "$GOH/lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)

VERSION="${1:-}"
VERSION="${VERSION#v}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    err "release_goh.sh: usage: tools/release_goh.sh X.Y.Z (got '${1:-}')"
    exit 2
fi
TAG="v$VERSION"
main="$(git rev-parse --show-toplevel)"
cd "$main"
if [ -n "$(GIT_OPTIONAL_LOCKS=0 git status --porcelain --untracked-files=no)" ]; then
    err "release_goh.sh: $main has uncommitted changes; a release names a commit -- nothing moved"
    exit 1
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    err "release_goh.sh: $TAG already exists; a tag is never moved -- nothing moved"
    exit 1
fi
# The preflight asks of the title the cut will write: goh ship's --summary, as the cut below.
title=()
[ -n "${GOH_SHIP_SUMMARY:-}" ] && title=(--title "$GOH_SHIP_SUMMARY")
if ! "$GOH_PY" "$GOH/tools/release-kit/changelog_cut.py" ready --version "$VERSION" ${title[@]+"${title[@]}"} --file CHANGELOG.md >&2; then
    err "release_goh.sh: CHANGELOG.md is not ready to release (above) -- nothing moved"
    exit 1
fi

wt="$main/build/release-$TAG"
branch="release-$TAG"
section "release $TAG from ${main}"
git worktree add -q -b "$branch" "$wt" HEAD
# The worktree and its branch go on every exit: landed, main holds the commit; refused, nothing moved.
trap 'git -C "$main" worktree remove --force "$wt" 2>/dev/null || true
      git -C "$main" branch -D "$branch" >/dev/null 2>&1 || true' EXIT

(
    cd "$wt"
    "$GOH_PY" - "$VERSION" <<'PY'
import re, sys
v = sys.argv[1]
p = "Cargo.toml"
s = open(p).read()
head, sep, rest = s.partition("[workspace.package]")
if not sep:
    sys.exit("release_goh.sh: Cargo.toml has no [workspace.package]")
rest, n = re.subn(r'(?m)^version = "[^"]*"', f'version = "{v}"', rest, count=1)
if n != 1:
    sys.exit("release_goh.sh: [workspace.package] declares no version")
open(p, "w").write(head + sep + rest)
PY
    if [ -f Cargo.lock ]; then
        cargo update --workspace --offline -q
    fi
    "$GOH_PY" "$GOH/tools/release-kit/changelog_cut.py" cut --version "$VERSION" ${title[@]+"${title[@]}"} --file CHANGELOG.md
    "$GOH_PY" "$GOH/tools/release-kit/changelog_cut.py" check --version "$VERSION" --file CHANGELOG.md
    heading="$(grep -m1 -E "^## v${VERSION//./\\.}( |$)" CHANGELOG.md)"
    git add Cargo.toml CHANGELOG.md
    [ -f Cargo.lock ] && git add Cargo.lock
    git commit -q -F - <<MSG
release: ${heading#\#\# }

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
    tools/land.sh
)

git push origin main
notes="$(mktemp "${TMPDIR:-/tmp}/goh-release-notes.XXXXXX")"
awk -v h="## $TAG" 'index($0, h) == 1 && (length($0) == length(h) || substr($0, length(h) + 1, 1) == " ") { on = 1; next }
    on && /^## / { exit } on { print }' CHANGELOG.md | sed -e '/./,$!d' >"$notes"
[ -s "$notes" ] || { err "release_goh.sh: the $TAG stanza is empty in the landed CHANGELOG.md"; rm -f "$notes"; exit 1; }
{ printf '%s\n\n' "$TAG"; cat "$notes"; } | git tag -a "$TAG" -F -
rm -f "$notes"
git push origin "refs/tags/$TAG"
ok "released $TAG at $(git rev-parse --short "$TAG^{commit}") (tag $(git rev-parse --short "$TAG"))"
exit
} # parse-guard
