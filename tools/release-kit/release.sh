#!/usr/bin/env bash
# tools/release-kit/release.sh — ONE parameterized releaser replacing seven
# per-repo copies that drifted (divoom-control, ztools, monitor, CadGoose,
# a private Rust app each grew their own; every one disagreed about ordering, gates and
# idempotency).
#
# Contract — steps run in this order, each skippable, every failure names its
# step:
#   0a. version   HEAD declares --version (`goh tag-version` over the tag that would be
#                 pushed, .gatesrc in its environment): never tag before the bump commit.
#   0. ci         every CI run for HEAD (`gh run list --commit`) complete and green (GitHub
#                 Actions DISABLED for the repo, read from its actions/permissions, is "no CI");
#                 HEAD not on origin/BRANCH is PUSHED first (the gated push) so CI runs on it,
#                 read ONCE, never waited on: red refuses, each run named (ztools tagged three
#                 over red CI); pending or none is ADVISORY over goh's push gate, refused
#                 without one (GOH.CIWAIT). No workflows: "no CI", not a green. (ci_step.sh)
#   1. gate       verify the local gate-of-record is green
#                 (--gate CMD or GOH_RELEASE_GATE; a release is only cut on a
#                 green gate of record)
#   1b. archive   --archive-build CMD: extract `git archive HEAD` into a temp
#                 dir and run CMD there. That tree is what a tarball consumer
#                 (a Homebrew formula, a source download) gets -- TRACKED files
#                 only. A path dependency or data file that is gitignored builds
#                 green locally and fails here, before it fails on someone
#                 else's machine (ztools shipped three tags that way).
#   1c. verify    --verify CMD: run CMD after the gate (install, then check the
#                 installed binary answers with GOH_RELEASE_VERSION, exercise a
#                 command added this cycle). A tag is a claim that this exact
#                 surface was run, not that it compiled; the claim is made here
#                 or the tag is not cut.
#   2. changelog  verify CHANGELOG.md has a stanza headed by --version
#                 (## vX.Y.Z or ## [X.Y.Z]); --no-changelog-check to skip.
#                 The stanza body becomes the tag message and release notes,
#                 so docs and releases cannot disagree. Unreleased must be EMPTY at the
#                 tagged commit and no entry may repeat an earlier stanza's
#                 (changelog_cut.py check). Entries under Unreleased: step 0b cuts them
#                 itself (--title TEXT, else the first entry's clause; or merged into a hand
#                 stanza) into an unpushed "chore: version X" HEAD, else "chore: changelog vX".
#   3. tag        create annotated tag vX.Y.Z if absent — idempotent: an
#                 existing tag is left alone, never moved or re-created
#   4. push       push origin's DEFAULT branch + tag, from any branch (branch_step.sh); --no-push skips
#   5. release    gh release create with the stanza body as notes — only when
#                 gh exists AND origin is a github.com remote; skipped with a
#                 stated reason otherwise; --skip-release to skip always.
#                 An existing GitHub release is left alone (idempotent).
#                 --no-push implies this step is skipped too: the tag was
#                 never pushed, so a GitHub release of it cannot be created
#                 (pass --skip-release as well if you want that stated
#                 explicitly instead of implied).
#                 BACKFILL first: every EARLIER v<version> tag on origin with
#                 no GitHub release (a tag cut by hand, bypassing this kit)
#                 gets one from its own CHANGELOG stanza, oldest first, with
#                 --latest=false -- so the tag cut now is created last and
#                 stays Latest. An earlier tag with no stanza is warned about
#                 by name, never fatal. --dry-run plans each candidate from the
#                 LOCAL tags (no network, so "if it has no release yet").
#   6. tap        bump a Homebrew formula/cask:
#                 --tap ztomer/homebrew-tap --formula NAME | --cask NAME
#                 computes sha256 of --artifact (a path or URL; default: the
#                 GitHub tag tarball), rewrites the url tag + sha256 lines in
#                 Formula/NAME.rb or Casks/NAME.rb, commits and pushes the tap
#                 clone. Unchanged formula → no commit (idempotent). --tap may
#                 also be a local git path (testing / private remotes).
#
# --dry-run prints the planned actions and performs NOTHING — no gate run, no
# tag, no push, no network. Dry-run is explicit; it never defaults on.
#
# Usage:
#   tools/release-kit/release.sh --version 1.2.3 --gate "make ci"
#   tools/release-kit/release.sh --version 1.2.3 --gate "$GOH/tools/gate.sh" \
#       --tap ztomer/homebrew-tap --cask myapp --artifact dist/MyApp-1.2.3.dmg
# Run HEAD, not the tree (C4) -- once: the self-buffered copy below runs from $TMPDIR, where
# `../../gates` is nothing, and its parent already chose the tree (tests/test_release_kit.py). The
# bash 5 guard runs first too (lib/bash5.sh); the buffered copy is exec'd by its "$BASH".
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; [ -n "${GOH_RELEASE_BUFFERED:-}" ] || { . "$_goh_d/../../lib/bash5.sh" || exit; goh_bash5 "$@"; . "$_goh_d/../../gates/_from_head.sh" || exit; goh_from_head "${BASH_SOURCE[0]}" "$@"; }
set -euo pipefail

# ── self-buffering (must run before ANY logic) ───────────────────────────────
# bash parses a script lazily, byte-offset by byte-offset as it executes.
# Editing this file WHILE an invocation runs shifts every unread offset —
# the field-reported mid-release parse errors and silent truncations. Guard:
# snapshot self to a temp copy BEFORE any logic, then exec that copy; the
# running release is pinned to an immutable byte image of startup time.
# Why a temp copy rather than `exec bash <(cat "$0")`: process substitution
# feeds the parser a live pipe/fd — $0 becomes /dev/fd/N (breaking --help's
# `sed ... "$0"` and every diagnostic naming the script) and parsing stays
# coupled to a concurrent writer instead of a finished snapshot. A temp copy
# needs nothing newer than macOS's stock bash 3.2 (no mapfile/readarray).
GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"
# shellcheck disable=SC1091
source "$GOH/tui/lib.sh"; . "$GOH/lib/tmp_root.sh"   # goh_tmp: every temp root under $TMPDIR/goh.noindex (GOH.TMPNOINDEX)
if [ -z "${GOH_RELEASE_BUFFERED:-}" ]; then
  _SELF_COPY="$(mktemp "$(goh_tmp)/goh-release-buffered.XXXXXX")"
  cat "$0" > "${_SELF_COPY}"
  export GOH_RELEASE_BUFFERED="${_SELF_COPY}"
  exec "$BASH" "${_SELF_COPY}" "$@"   # bash >= 5: the guard above ran first
fi
_SELF_COPY="${GOH_RELEASE_BUFFERED}"
unset GOH_RELEASE_BUFFERED

VERSION=""
GATE_CMD="${GOH_RELEASE_GATE:-}"
BRANCH=""            # --branch NAME; default: origin's default branch (branch_step.sh)
CHANGELOG_CHECK=1; TITLE="${GOH_SHIP_SUMMARY:-}"   # goh ship --summary titles the stanza (GOH.SHIPSUMMARY); --title wins
CI_CHECK=1
DO_PUSH=1
DO_RELEASE=1
DRY_RUN=0
CUT_PLANNED=0        # set by step 0b in a dry run: the stanza it would cut
TAP=""
TAP_KIND=""          # formula | cask
TAP_NAME=""
ARTIFACT=""
ARCHIVE_BUILD=""
VERIFY_CMD=""

die_usage() { err "usage: $0 --version X.Y.Z [--gate CMD] [--dry-run] ...  (see header)"; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version)            VERSION="${2:?}"; shift 2 ;;
    --gate)               GATE_CMD="${2:?}"; shift 2 ;;
    --branch)             BRANCH="${2:?}"; shift 2 ;;
    --no-changelog-check) CHANGELOG_CHECK=0; shift ;;
    --title)              TITLE="${2:?}"; shift 2 ;;
    --no-ci-check)        CI_CHECK=0; shift ;;
    --no-push)            DO_PUSH=0; shift ;;
    --skip-release)       DO_RELEASE=0; shift ;;
    --dry-run)            DRY_RUN=1; shift ;;
    --tap)                TAP="${2:?}"; shift 2 ;;
    --formula)            TAP_KIND="formula"; TAP_NAME="${2:?}"; shift 2 ;;
    --cask)               TAP_KIND="cask"; TAP_NAME="${2:?}"; shift 2 ;;
    --artifact)           ARTIFACT="${2:?}"; shift 2 ;;
    --archive-build)      ARCHIVE_BUILD="${2:?}"; shift 2 ;;
    --verify)             VERIFY_CMD="${2:?}"; shift 2 ;;
    -h|--help)            sed -n '2,69p' "$0"; exit 0 ;;
    *) die_usage ;;
  esac
done
[ -n "$VERSION" ] || die_usage
# X.Y.Z (semver) or X.Y — some repos' tag scheme is two-component (CadGoose:
# v1.71 … v1.79); the old three-glob check rejected their entire history.
# A here-string, never `printf | grep -q`: under pipefail that is a race (goh early-exit-pipe).
if grep -Eq '^[0-9]+\.[0-9]+(\.[0-9]+)?$' <<<"$VERSION"; then :; else
  die "step 'args': --version must look like 1.2.3 or 1.79 (got '$VERSION')"
fi
if [ -n "$TAP" ] && [ -z "$TAP_NAME" ]; then
  die "step 'args': --tap needs --formula NAME or --cask NAME"
fi
if [ -n "$TAP_NAME" ] && [ -z "$TAP" ]; then
  die "step 'args': --formula/--cask need --tap ORG/REPO"
fi

TAG="v${VERSION}"
BODY="" NOTES="" BACKFILL_NOTES="" TAP_DIR="" ARCHIVE_DIR=""
cleanup() {
  # Every branch must succeed — this trap's status becomes the script's.
  [ -z "$BODY" ] || rm -f "$BODY"
  [ -z "$NOTES" ] || rm -f "$NOTES"
  [ -z "$BACKFILL_NOTES" ] || rm -f "$BACKFILL_NOTES"
  [ -z "$TAP_DIR" ] || rm -rf "$TAP_DIR"
  [ -z "$ARCHIVE_DIR" ] || rm -rf "$ARCHIVE_DIR"  # a build that fails exits before its own rm
  [ -z "${_SELF_COPY:-}" ] || rm -f "${_SELF_COPY}"
  true
}
trap cleanup EXIT
STEP=""
plan() { info "[dry-run] $*"; }
begin() { STEP="$1"; info "$1: $2"; }
fail() { err "step '${STEP}' failed: $1"; exit 1; }
SKIPPED=""
note_skip() {
  # Every skipped step is announced inline AND repeated in the run summary.
  SKIPPED="${SKIPPED:+${SKIPPED}; }$1"
  warn "skipped ($2)"
}

# The stanza readers, github_slug, the backfill and the release cut: tools/release-kit/gh_release.sh,
# shared with goh's own kit (tools/release_goh.sh, GOH.SELFGHRELEASE).
# shellcheck source=tools/release-kit/gh_release.sh
source "$GOH/tools/release-kit/gh_release.sh"

# ── repo preconditions ───────────────────────────────────────────────────────
git rev-parse --show-toplevel >/dev/null 2>&1 \
  || die "precondition: not inside a git repository"
[ -f CHANGELOG.md ] || warn "no CHANGELOG.md in $(pwd) — the changelog step will fail"
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
[ -n "$ORIGIN_URL" ] || warn "no 'origin' remote — push and release steps will fail or skip"
GH_REPO_SLUG="$(github_slug "$ORIGIN_URL")"
[ -n "$GH_REPO_SLUG" ] \
  || GH_REPO_SLUG="$(github_slug "$(git config --get remote.origin.url 2>/dev/null || true)")"

# shellcheck source=tools/release-kit/branch_step.sh
source "$GOH/tools/release-kit/branch_step.sh"
release_branch_step
if [ "$DRY_RUN" = 1 ]; then
  section "release plan for ${TAG} (dry-run — nothing will execute)"
else
  section "release ${TAG} to ${BRANCH}"
fi

# ── 0a. HEAD declares the version being tagged (tools/release-kit/version_step.sh) ──
# shellcheck source=tools/release-kit/version_step.sh
source "$GOH/tools/release-kit/version_step.sh"
release_version_step
release_refusals_first  # every tree-decided refusal before the first commit or push (GOH.KITORDER)
# ── 0b. the stanza is cut from Unreleased by the kit, not refused (GOH.CUTAUTO) ──
release_changelog_cut || { _cut_rc=$?; [ "$_cut_rc" -eq 3 ] || exit "$_cut_rc"; CUT_PLANNED=1; }

# ── 0. CI on the commit being tagged ─────────────────────────────────────────
# First, because a red answer spares the gate and every step after it. An unpushed HEAD is pushed
# (gated) and its runs read once, never waited on: tools/release-kit/ci_step.sh.
# shellcheck source=tools/release-kit/ci_step.sh
source "$GOH/tools/release-kit/ci_step.sh"
release_ci_step "$CI_CHECK"

# ── 1. gate ──────────────────────────────────────────────────────────────────
begin "gate" "verify the local gate of record"
# A repo declares its release gate ONCE, in its .gatesrc (GOH_RELEASE_GATE; GOH.SHX2), resolved with
# the backfill floor by release_refusals_first before anything was committed or pushed.
if [ "$DRY_RUN" = 1 ]; then
  plan "run gate: ${GATE_CMD}"
else
  bash -c "$GATE_CMD" || fail "gate command exited nonzero (${GATE_CMD})"
  ok "gate green"
fi

# ── 1b. the archived tree builds ─────────────────────────────────────────────
if [ -n "$ARCHIVE_BUILD" ]; then
  begin "archive" "build from git archive HEAD (tracked files only)"
  if [ "$DRY_RUN" = 1 ]; then
    plan "git archive HEAD | tar -x into a temp dir; run: ${ARCHIVE_BUILD}"
  else
    ARCHIVE_DIR="$(mktemp -d "$(goh_tmp)/goh-release-archive.XXXXXX")"
    git archive --format=tar HEAD | tar -x -C "$ARCHIVE_DIR" \
      || fail "git archive HEAD could not be extracted"
    ( cd "$ARCHIVE_DIR" && GOH_RELEASE_VERSION="$VERSION" bash -c "$ARCHIVE_BUILD" ) \
      || fail "the archived tree does not build (${ARCHIVE_BUILD}) -- a tracked-files-only \
checkout is what the tarball ships; look for a gitignored path dependency or data file"
    rm -rf "$ARCHIVE_DIR"
    ok "archive builds"
  fi
fi

# ── 1c. install + exercise what is about to be tagged ────────────────────────
if [ -n "$VERIFY_CMD" ]; then
  begin "verify" "install and exercise the release surface"
  if [ "$DRY_RUN" = 1 ]; then
    plan "run verify (GOH_RELEASE_VERSION=${VERSION}): ${VERIFY_CMD}"
  else
    GOH_RELEASE_VERSION="$VERSION" bash -c "$VERIFY_CMD" \
      || fail "verify command exited nonzero (${VERIFY_CMD}) -- nothing tagged"
    ok "verified"
  fi
fi

# ── 2. changelog stanza ──────────────────────────────────────────────────────
begin "changelog" "look for a ${TAG} stanza in CHANGELOG.md"
if [ "$CHANGELOG_CHECK" = 0 ]; then
  warn "skipped (--no-changelog-check)"
elif [ "${CUT_PLANNED:-0}" = 1 ]; then
  plan "the ${TAG} stanza is the one step 0b makes from Unreleased (changelog_cut.py check runs on it)"
elif has_stanza "$VERSION"; then
  # The tag ships the whole tree, so at this commit nothing is unreleased: an Unreleased section
  # that still holds entries, or a stanza repeating an earlier one's entry, is refused (GOH.REL2:
  # goh's Unreleased carried twelve entries v0.60.0-v0.63.0 had shipped). changelog_cut.py cut
  # makes the stanza FROM Unreleased.
  bash "$GOH/gates/python.sh" "$GOH/tools/release-kit/changelog_cut.py" check --version "$VERSION" ${FRAGS:+--fragments "$FRAGS"} --file CHANGELOG.md \
    || fail "CHANGELOG.md does not hold for ${TAG} (above) -- run tools/release-kit/changelog_cut.py cut --version ${VERSION} --title ... and commit; nothing tagged"
  ok "found; Unreleased is empty and no entry repeats an earlier stanza"
else
  fail "CHANGELOG.md has no stanza for ${VERSION} (add '## ${TAG}' first, or: tools/release-kit/changelog_cut.py cut --version ${VERSION} --title ...)"
fi

# ── 3. annotated tag (idempotent, but NEVER stale) ───────────────────────────
begin "tag" "annotated tag ${TAG}"
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  # An existing tag is left alone ONLY if it names HEAD. A tag pinned to an
  # older commit while work moved on is not idempotency — it would release
  # (and push, and tap-bump) the WRONG tree while reporting success.
  tagged_commit="$(git rev-parse "refs/tags/${TAG}^{commit}")"
  head_commit="$(git rev-parse HEAD)"
  if [ "$tagged_commit" != "$head_commit" ]; then
    fail "${TAG} exists at ${tagged_commit} but HEAD is ${head_commit} — \
bump --version, or move the tag deliberately: git tag -f ${TAG} && git push -f origin ${TAG}"
  fi
  ok "${TAG} already exists — leaving it (idempotent)"
elif [ "$DRY_RUN" = 1 ]; then
  plan "git tag -a ${TAG} at $(git rev-parse --short HEAD)"
else
  BODY="$(mktemp "$(goh_tmp)/goh-release-body.XXXXXX")"
  stanza_body "$VERSION" > "$BODY"
  [ -s "$BODY" ] || printf '%s\n' "${TAG}" > "$BODY"
  # --cleanup=verbatim: without it git strips '#' lines from -F messages as
  # commentary, silently deleting every ### subsection of a Keep-a-changelog
  # stanza from the tag message.
  git tag -a "$TAG" --cleanup=verbatim -F "$BODY" || fail "git tag -a ${TAG} exited nonzero"
  ok "tagged $(git rev-parse --short HEAD)"
fi

# ── 4. push branch + tag ─────────────────────────────────────────────────────
begin "push" "branch ${BRANCH} + ${TAG} to origin"
if [ "$DO_PUSH" = 0 ]; then
  note_skip "push (--no-push)" "--no-push"
elif [ "$DRY_RUN" = 1 ]; then
  plan "git push origin ${PUSH_REFSPEC} ${TAG}"
else
  # ONE push: the pre-push gate refuses a branch that carries a local release tag the remote
  # lacks (goh tag-version --remote), which is what the first of two pushes always was.
  git push origin "$PUSH_REFSPEC" "$TAG" || fail "could not push ${BRANCH} + ${TAG}"
  # origin is read back before "pushed" is said (G15): a push can return 0 over a remote that did not move.
  remote_head="$(git ls-remote origin "refs/heads/${BRANCH}" | cut -f1)" || fail "git ls-remote origin exited nonzero"
  remote_tag="$(git ls-remote origin "refs/tags/${TAG}" | cut -f1)" || fail "git ls-remote origin exited nonzero"
  [ "$remote_head" = "$(git rev-parse HEAD)" ] \
    || fail "origin/${BRANCH} is ${remote_head:-missing}, not $(git rev-parse HEAD), after a push that returned 0 -- not pushed"
  [ "$remote_tag" = "$(git rev-parse "refs/tags/${TAG}")" ] \
    || fail "origin has ${TAG} at ${remote_tag:-nothing}, not $(git rev-parse "refs/tags/${TAG}"), after a push that returned 0 -- not pushed"
  ok "pushed"
fi

# ── 5. GitHub release (earlier release-less tags first) ─────────────────────
begin "release" "gh release create ${TAG}"
if [ "$DO_RELEASE" = 0 ]; then
  note_skip "release (--skip-release)" "--skip-release"
elif [ "$DO_PUSH" = 0 ]; then
  # Implied skip: gh release create needs the tag on github.com, and --no-push
  # just declined to put it there. Announce the implication rather than
  # failing (or worse, "succeeding" against an unpushed tag).
  note_skip "release (implied by --no-push: ${TAG} was never pushed)" \
    "--no-push implies no GitHub release of unpushed ${TAG}"
elif ! command -v gh >/dev/null; then
  warn "skipped: gh CLI not installed"
elif [ -z "$GH_REPO_SLUG" ]; then
  warn "skipped: origin (${ORIGIN_URL}) is not a github.com remote"
elif [ "$DRY_RUN" = 1 ]; then
  backfill_releases
  plan "gh release create ${TAG} --notes-file <stanza body> (repo ${GH_REPO_SLUG})"
else
  # Backfills first, each --latest=false: the API makes a new release Latest
  # unless told otherwise, so the release cut NOW is created last and stays it.
  backfill_releases
  cut_release
fi

# ── 6. Homebrew tap bump ─────────────────────────────────────────────────────
if [ -z "$TAP_NAME" ]; then
  info "tap: not requested — skipping"
else
  begin "tap" "${TAP_KIND} ${TAP_NAME} in ${TAP}"
  if [ -z "$ARTIFACT" ] && [ -n "$GH_REPO_SLUG" ]; then
    ARTIFACT="https://github.com/${GH_REPO_SLUG}/archive/refs/tags/${TAG}.tar.gz"
  elif [ -z "$ARTIFACT" ] && [ "$DRY_RUN" = 1 ]; then
    ARTIFACT="(no --artifact, and origin is not github.com)"
  elif [ -z "$ARTIFACT" ]; then
    fail "no --artifact given and origin is not github.com — cannot derive tarball URL"
  fi
  # The house tap bump: clone, edit, the TAP's own gate, a pinned push (BACKLOG 6.8).
  tap_args=(--tap "$TAP" "--${TAP_KIND}" "$TAP_NAME" --tag "$TAG" --artifact "$ARTIFACT")
  [ "$DRY_RUN" = 1 ] && tap_args+=(--dry-run)
  GOH_DIR="$GOH" bash "$GOH/tools/release-kit/tap_bump.sh" "${tap_args[@]}" \
    || fail "the tap bump refused (above)"
fi

hr
if [ -n "$SKIPPED" ]; then
  info "skipped steps → ${SKIPPED}"
fi
if [ "$DRY_RUN" = 1 ]; then
  ok "release ${TAG} planned (dry-run — nothing was executed)"
else
  ok "release ${TAG} complete"
fi
