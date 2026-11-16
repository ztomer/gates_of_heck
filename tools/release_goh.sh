#!/usr/bin/env bash
# release_goh.sh VERSION -- gates_of_heck's own release kit, `goh ship`'s GOH_SHIP_RELEASE (GOH.SHIPKIT).
#
# goh ship could not release goh: no kit was declared, and v0.95.0's bump (8e90921) was made by
# hand. This does what that commit did, the house way:
#
#   1. refuse, before anything moves, a dirty tree, a VERSION that is not X.Y.Z, a tag that already
#      exists, and an Unreleased with nothing to release (changelog_cut.py ready);
#   2. in a worktree off HEAD under the run root (lib/run_path.sh), never in the checkout:
#      Unreleased cut into `## vVERSION` (titled with GOH_SHIP_SUMMARY when ship gives one), one
#      `release: vVERSION -- TITLE` commit through the hooks. Nothing else: the version is the tag
#      (lib/goh_version.sh), never a number in Cargo.toml (GOH.SHIPRELREUSE), and `goh tag-version`
#      reads the release from this commit's CHANGELOG.md (.gatesrc GOH_TAG_VERSION_SOURCES);
#   3. the tip's loaded verdict (lib/loaded_verdict.py: the suite once under tools/load.py's
#      spinners, GOH.CLASS.HARNESS, run by the land that landed the tip -- GOH.SHIPRELREUSE R2),
#      then tools/land.sh from that worktree, gated by lib/release_gate.py (the structural gate and
#      the tests that read the release files when that is all the commit changed, R3): main moves
#      only through land (.gatesrc GOH_LANDED_ONLY), never a commit in the main checkout and
#      never GOH_MAIN_COMMIT_OK;
#   4. push main, then an annotated tag vVERSION whose message is the stanza, then push the tag.
#
# With .gatesrc GOH_CHANGELOG_FRAGMENTS=on (goh's own), each change's entry is its own file in
# changelog/ (tools/release-kit/changelog_fragments.py): the preflight and the cut fold them into
# Unreleased, and the release commit deletes the fragments it folded.
#
# It pushes, so .gatesrc says GOH_SHIP_RELEASE_PUSHES=1. Pinned by tests/test_release_goh.py.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=lib/house_python.sh
. "$GOH/lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
# shellcheck source=lib/run_path.sh
. "$GOH/lib/run_path.sh" || exit 1

VERSION="${1:-}"
VERSION="${VERSION#v}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    err "release_goh.sh: usage: tools/release_goh.sh X.Y.Z (got '${1:-}')"
    exit 2
fi
TAG="v$VERSION"
timed() { "$GOH_PY" "$GOH/lib/step_timings.py" time "$1" -- "${@:2}"; }   # one profile row
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
frag=()
frag_on="$(bash -c 'unset GOH_CHANGELOG_FRAGMENTS; [ -f .gatesrc ] && . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_CHANGELOG_FRAGMENTS-}"')"
case "$frag_on" in
    on) frag=(--fragments changelog) ;;
    "" | off) ;;
    *) err "release_goh.sh: GOH_CHANGELOG_FRAGMENTS is neither on nor off: '$frag_on' -- nothing moved"; exit 2 ;;
esac
if ! "$GOH_PY" "$GOH/tools/release-kit/changelog_cut.py" ready --version "$VERSION" "${title[@]}" "${frag[@]}" --file CHANGELOG.md >&2; then
    err "release_goh.sh: CHANGELOG.md is not ready to release (above) -- nothing moved"
    exit 1
fi

# The worktree is the checkout's `release` directory under the run root: outside its work tree (as
# build/release-vX it was a second checkout inside the first), and ONE stable path, so the land
# gate's cargo build-dir (keyed by workspace path) is the last release's, built incrementally.
wt="$(goh_run_dir "$main")/release"
branch="release-$TAG"
section "release $TAG from ${main}"
if [ -e "$wt" ]; then  # a release killed before its trap ran
    git worktree remove --force "$wt" 2>/dev/null || true
    git worktree prune
    if [ -e "$wt" ]; then
        err "release_goh.sh: $wt is left from an earlier release and is no worktree git can remove -- nothing moved"
        exit 1
    fi
fi
tip="$(git rev-parse HEAD)"   # the land-gated tip the release is cut from: its loaded verdict
git worktree add -q -b "$branch" "$wt" "$tip"
# The worktree and its branch go on every exit: landed, main holds the commit; refused, nothing moved.
trap 'git -C "$main" worktree remove --force "$wt" 2>/dev/null || true
      git -C "$main" branch -D "$branch" >/dev/null 2>&1 || true' EXIT

(
    cd "$wt"
    # No version file is bumped: goh's version IS the tag (lib/goh_version.sh, GOH.SHIPRELREUSE),
    # so this commit changes only what the changelog cut writes and folds, and the tip it was cut
    # from was judged on every byte a build or a test outside the changelog reads.
    "$GOH_PY" "$GOH/tools/release-kit/changelog_cut.py" cut --version "$VERSION" "${title[@]}" "${frag[@]}" --file CHANGELOG.md
    if [ "${#frag[@]}" -gt 0 ]; then  # the stanza holds them now; the release commit deletes them
        mapfile -t folded < <("$GOH_PY" "$GOH/tools/release-kit/changelog_cut.py" fragments "${frag[@]}")
        [ "${#folded[@]}" -eq 0 ] || git rm -q -- "${folded[@]}"
    fi
    "$GOH_PY" "$GOH/tools/release-kit/changelog_cut.py" check --version "$VERSION" "${frag[@]}" --file CHANGELOG.md
    heading="$(grep -m1 -E "^## v${VERSION//./\\.}( |$)" CHANGELOG.md)"
    git add CHANGELOG.md
    git commit -q -F - <<MSG
release: ${heading#\#\# }

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
    # The suite once under deliberate load, before main moves (GOH.CLASS.HARNESS): a test the box's
    # load turns red is this release's refusal, named, not a random later land's. It is no longer
    # RUN here (485-511 s of every release step): each land starts it on the tip it landed and
    # records the verdict by tree (.gatesrc GOH_LAND_LOADED, lib/loaded_verdict.py), and this
    # release's commit differs from that tip only in the changelog, so the tip's verdict is read:
    # waited for while it runs, refused red, died or missing (GOH.SHIPRELREUSE R2). Each phase is
    # one row of goh ship's profile (timed: lib/step_timings.py, GOH.SHIPPROFILE). Under goh ship
    # this read is instant: the ship worker waited for the verdict under the land lock before its
    # first step (tools/ship.py before_clock, GOH.SHIPLOADEDWAIT), never inside this step's clock.
    # The verdict of the command land started (GOH_LAND_LOADED): the record is keyed by it (GOH.VERDICTKEY).
    loaded="$(bash -c 'unset GOH_LAND_LOADED; [ -f .gatesrc ] && . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_LOADED-}"')"
    timed "release: the tip's loaded verdict" "$GOH_PY" "$GOH/lib/loaded_verdict.py" wait \
        --repo "$main" --sha "$tip" --max 3600 ${loaded:+--cmd "$loaded"}
    # The land's gate (GOH.SHIPRELREUSE R3): this commit changes only release files, so it runs the
    # structural gate and the tests that read them (lib/release_gate.py); a diff since the tip with
    # anything else in it -- main moved and land rebased -- runs the full gate land would have run.
    full="$(bash -c 'unset GOH_LAND_GATE; [ -f .gatesrc ] && . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_GATE-}"')"
    [ -n "$full" ] || full="$GOH/tools/gate_profile.sh ."
    release_gate="$(printf '%q ' "$GOH_PY" "$GOH/lib/release_gate.py" run --base "$tip" --full "$full")"
    GOH_LAND_GATE="${release_gate% }" timed "release: land (its release gate)" tools/land.sh
)

# Its push gate takes the same reduced set: release files only on a parent whose land was green
# (.gatesrc GOH_PUSH_RELEASE_REUSE, lib/release_gate.py push-decide; anything else is the full gate).
timed "release: push main" git push origin main
notes="$(mktemp "${TMPDIR:-/tmp}/goh-release-notes.XXXXXX")"
awk -v h="## $TAG" 'index($0, h) == 1 && (length($0) == length(h) || substr($0, length(h) + 1, 1) == " ") { on = 1; next }
    on && /^## / { exit } on { print }' CHANGELOG.md | sed -e '/./,$!d' >"$notes"
[ -s "$notes" ] || { err "release_goh.sh: the $TAG stanza is empty in the landed CHANGELOG.md"; rm -f "$notes"; exit 1; }
{ printf '%s\n\n' "$TAG"; cat "$notes"; } | git tag -a "$TAG" -F -
rm -f "$notes"
timed "release: push the tag" git push origin "refs/tags/$TAG"
ok "released $TAG at $(git rev-parse --short "$TAG^{commit}") (tag $(git rev-parse --short "$TAG"))"
exit
} # parse-guard
