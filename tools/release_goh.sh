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
#   3. tools/land.sh from that worktree, gated by lib/release_gate.py (the structural gate and
#      the tests that read the release files when that is all the commit changed, R3): main moves
#      only through land (.gatesrc GOH_LANDED_ONLY), never a commit in the main checkout and
#      never GOH_MAIN_COMMIT_OK;
#   4. push main, then an annotated tag vVERSION whose message is the stanza, then push the tag;
#   5. the GitHub release of vVERSION from its stanza, after backfilling every earlier tag on origin
#      with none (tools/release-kit/gh_release.sh, release.sh's own step; GOH.SELFGHRELEASE).
#
# With .gatesrc GOH_CHANGELOG_FRAGMENTS=on (goh's own), each change's entry is its own file in
# changelog/ (tools/release-kit/changelog_fragments.py): the preflight and the cut fold them into
# Unreleased, and the release commit deletes the fragments it folded.
#
# It pushes, so .gatesrc says GOH_SHIP_RELEASE_PUSHES=1. Pinned by tests/test_release_goh.py.
#
# `tools/release_goh.sh VERSION`: goh's own `goh ship` release kit (`.gatesrc` GOH_SHIP_RELEASE,
# GOH_SHIP_RELEASE_PUSHES=1; GOH.SHIPKIT): refuses a dirty tree, an existing tag and an empty
# Unreleased before anything moves; in a `build/` worktree bumps `[workspace.package]` and
# Cargo.lock, cuts Unreleased into `## vVERSION` and commits; lands through `tools/land.sh`, gated
# by `lib/release_gate.py` (the structural gate and the release files' readers for a
# release-files-only commit, R3); pushes main; tags `vVERSION` with the stanza as its message and
# pushes the tag. Pinned by `test_release_goh.py`.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"; . "$GOH/lib/tmp_root.sh"   # goh_tmp: every temp root under $TMPDIR/goh.noindex, never Spotlight-indexed (GOH.TMPNOINDEX)
# shellcheck source=lib/house_python.sh
. "$GOH/lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
# shellcheck source=lib/run_path.sh
. "$GOH/lib/run_path.sh" || exit 1

# --preflight VERSION (GOH.SHIPEARLY; .gatesrc GOH_SHIP_PREFLIGHT, which `goh ship` asks before its
# suite): every check below that needs no suite verdict and no built goh, then the release commit
# itself, made in a scratch worktree off HEAD and judged by the release land's own gate
# (lib/release_gate.py run). Its green is recorded for that tree (`relset`), so the release land of
# the same tree reuses it rather than running it again; its red refuses the ship before the suite,
# where it used to refuse after it (v0.140.3: a heading the structural gate refused, 1019 s in).
PREFLIGHT=0
if [ "${1:-}" = --preflight ]; then PREFLIGHT=1; shift; fi
VERSION="${1:-}"
VERSION="${VERSION#v}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    err "release_goh.sh: usage: tools/release_goh.sh [--preflight] X.Y.Z (got '${1:-}')"
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
# A FULL SUITE VERDICT ON THE TIP (GOH.VERDICTCARRY): a land's suite carries verdicts from other
# trees (tests/_land_verdicts.py), so a land's green is no proof that every test ran on this tree.
# A repo that carries declares GOH_SHIP_FULL_SUITE=on, and its release needs a run that judged
# every test here itself, against the INSTRUMENTED binary (lib/reuse.py full-suite; GOH.COVCARRY,
# the owner's term 2); the idle lander makes it off the ship's path (lib/idle_suite.py).
full_on="$(bash -c 'unset GOH_SHIP_FULL_SUITE; [ -f .gatesrc ] && . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_SHIP_FULL_SUITE-}"')"
if [ "$PREFLIGHT" = 0 ] && [ "$full_on" = on ] && ! "$GOH_PY" "$GOH/lib/reuse.py" full-suite --repo "$main" --sha "$(git rev-parse HEAD)" >/dev/null; then
    err "release_goh.sh: no full suite run judged $(git rev-parse --short HEAD)'s tree (above): goh ship's suite step makes one under the land lock (lib/ship_full_run.py; the idle lander's lib/idle_suite.py INSTRUMENTED, run with GOH_LAND_VERDICTS_RECORD=/dev/null in a clean checkout of it) -- nothing moved"
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
# gate's cargo build-dir (keyed by workspace path) is the last release's, built incrementally. The
# preflight's is its own (`release-preflight`), so a preflight never touches a release's.
wt="$(goh_run_dir "$main")/release"
branch="release-$TAG"
if [ "$PREFLIGHT" = 1 ]; then
    wt="$(goh_run_dir "$main")/release-preflight"
    branch="release-preflight-$TAG"
    section "release preflight $TAG from ${main}"
else
    section "release $TAG from ${main}"
fi
if [ -e "$wt" ]; then  # a release killed before its trap ran
    git worktree remove --force "$wt" 2>/dev/null || true
    git worktree prune
    if [ -e "$wt" ]; then
        err "release_goh.sh: $wt is left from an earlier release and is no worktree git can remove -- nothing moved"
        exit 1
    fi
fi
git branch -D "$branch" >/dev/null 2>&1 || true   # a killed run's branch, never anything else's
tip="$(git rev-parse HEAD)"   # the land-gated tip the release is cut from: its release gate's base
git worktree add -q -b "$branch" "$wt" "$tip"
# The worktree and its branch go on every exit: landed, main holds the commit; refused, nothing moved.
# The tagged build below is waited for, never left running past the kit (it only fills the store).
prebuild="" prebuild_log=""
trap '[ -z "$prebuild" ] || wait "$prebuild" 2>/dev/null || true
      [ -z "$prebuild_log" ] || rm -f "$prebuild_log"
      git -C "$main" worktree remove --force "$wt" 2>/dev/null || true
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
)
# The land's gate (GOH.SHIPRELREUSE R3): this commit changes only release files, so it runs the
# structural gate and the tests that read them (lib/release_gate.py); a diff since the tip with
# anything else in it -- main moved and land rebased -- runs the full gate land would have run.
full="$(bash -c 'unset GOH_LAND_GATE; [ -f .gatesrc ] && . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_GATE-}"')"
[ -n "$full" ] || full="$GOH/tools/gate_profile.sh ."
if [ "$PREFLIGHT" = 1 ]; then
    # The same gate over the same tree, with the same base; a commit that is not release files
    # only (it cannot be, cut from HEAD) is refused here, never handed the full gate before the suite.
    (cd "$wt" && GOH_LAND_GATE="" timed "release preflight: the release gate" \
        "$GOH_PY" "$GOH/lib/release_gate.py" run --base "$tip" \
        --full "echo 'release_goh.sh: the release commit changes more than release files' >&2; exit 1") || {
        err "release_goh.sh: the release commit of $TAG fails its land's gate (above) -- refused before the suite, nothing moved"
        exit 1
    }
    ok "release preflight: $TAG's release commit passes its land's gate (recorded for that tree)"
    exit 0
fi
# THE TAGGED BUILD, BESIDE THE LAND AND THE PUSH (GOH.TAGPUSHGATE). A tagged commit's
# stamp names its tag, so its goh is a build of its own; the first gate to ask for it was the tag
# push's tag check, which rebuilt goh there: 98.6 s of v0.125.0's ship at load 190, on the critical
# path. Built here as the tag it will carry (build-goh.sh --as-release, into the store), it is ready
# when the tag is made. Only the store is written, keyed by that stamp: a release refused below, or
# one whose land rebased onto other build inputs, leaves an entry no stamp asks for, and the tag
# push builds what it needs as before.
prebuild_log="$(mktemp "$(goh_tmp)/goh-release-build.XXXXXX")"
bash "$main/scripts/build-goh.sh" --rev "$(git -C "$wt" rev-parse HEAD)" --as-release "$TAG" \
    >"$prebuild_log" 2>&1 </dev/null &
prebuild=$!
info "release: building goh as $TAG beside the land (pid $prebuild)"
(
    cd "$wt"
    # No loaded verdict is read (GOH.LOADEDDROP): the post-land loaded run caught nothing in 13
    # runs and cost every ship its wait; tests/test_load_sensitive.py and the wall-clock ratchet
    # keep the static guard, at commit.
    # The land's gate: `full` above, the release set when this commit is release files only.
    release_gate="$(printf '%q ' "$GOH_PY" "$GOH/lib/release_gate.py" run --base "$tip" --full "$full")"
    # NO PRE-STEP (GOH.RELEASELANDGATE): .gatesrc's GOH_LAND_PRE is a lane's fail-fast before its
    # full gate; here the gate IS the short one, and the pre-step ran 925 tree-reader tests over the
    # tree the tip's land had judged, 139-261 s of every release's 160-280 s land (v0.139.4: 139 of
    # 160 s), for a release gate that then reused them. Set empty, land.sh runs none (gatesrc_var);
    # a land that rebased gets release_gate.py's full gate, which needs no fail-fast before it.
    GOH_LAND_PRE="" GOH_LAND_GATE="${release_gate% }" timed "release: land (its release gate)" tools/land.sh
)

# Its push gate takes the same reduced set: release files only on a parent whose land was green
# (.gatesrc GOH_PUSH_RELEASE_REUSE, lib/release_gate.py push-decide; anything else is the full gate).
timed "release: push main" env GATES_OF_HECK_COUNTS_ADVISORY=release git push origin main
notes="$(mktemp "$(goh_tmp)/goh-release-notes.XXXXXX")"
awk -v h="## $TAG" 'index($0, h) == 1 && (length($0) == length(h) || substr($0, length(h) + 1, 1) == " ") { on = 1; next }
    on && /^## / { exit } on { print }' CHANGELOG.md | sed -e '/./,$!d' >"$notes"
[ -s "$notes" ] || { err "release_goh.sh: the $TAG stanza is empty in the landed CHANGELOG.md"; rm -f "$notes"; exit 1; }
{ printf '%s\n\n' "$TAG"; cat "$notes"; } | git tag -a "$TAG" -F -
rm -f "$notes"
prebuild_rc=0
wait "$prebuild" || prebuild_rc=$?
prebuild=""
if [ "$prebuild_rc" -eq 0 ]; then
    ok "release: goh built as $TAG ($(tail -n 1 "$prebuild_log"))"
else
    warn "release: the build of goh as $TAG failed (exit $prebuild_rc); the tag push builds it:"
    tail -n 5 "$prebuild_log" | sed 's/^/    /' >&2
fi
timed "release: push the tag" env GATES_OF_HECK_COUNTS_ADVISORY=release git push origin "refs/tags/$TAG"
# THE GITHUB RELEASE (GOH.SELFGHRELEASE). From GOH.SHIPKIT on this kit pushed the tag and made no
# release, so v0.96.0.. stood release-less and `gh release list` kept v0.95.0 Latest: the state the
# consumer kit backfills. The same step as release.sh's (tools/release-kit/gh_release.sh): every
# earlier release-less tag on origin backfilled with --latest=false, then this tag's release cut
# from its stanza and read back, so the newest tag is Latest.
STEP="release" DRY_RUN=0 BACKFILL_NOTES="" NOTES=""
plan() { info "[dry-run] $*"; }
fail() { err "release_goh.sh: step '${STEP}' failed: $1 ($TAG is pushed; the next release backfills its GitHub release)"; exit 1; }
trap '[ -z "$BACKFILL_NOTES" ] || rm -f "$BACKFILL_NOTES"; [ -z "$NOTES" ] || rm -f "$NOTES"
      [ -z "$prebuild_log" ] || rm -f "$prebuild_log"
      git -C "$main" worktree remove --force "$wt" 2>/dev/null || true
      git -C "$main" branch -D "$branch" >/dev/null 2>&1 || true' EXIT
# shellcheck source=tools/release-kit/gh_release.sh
. "$GOH/tools/release-kit/gh_release.sh"
# shellcheck source=tools/release-kit/version_step.sh
. "$GOH/tools/release-kit/version_step.sh"
release_backfill_floor   # GOH_RELEASE_BACKFILL_FLOOR -> BACKFILL_FLOOR
GH_REPO_SLUG="$(github_slug "$(git remote get-url origin 2>/dev/null || true)")"
[ -n "$GH_REPO_SLUG" ] || GH_REPO_SLUG="$(github_slug "$(git config --get remote.origin.url 2>/dev/null || true)")"
if ! command -v gh >/dev/null; then
    warn "release: no GitHub release of $TAG: gh CLI not installed"
elif [ -z "$GH_REPO_SLUG" ]; then
    warn "release: no GitHub release of $TAG: origin is not a github.com remote"
else
    backfill_releases
    cut_release
fi
ok "released $TAG at $(git rev-parse --short "$TAG^{commit}") (tag $(git rev-parse --short "$TAG"))"
exit
} # parse-guard
