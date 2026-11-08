#!/usr/bin/env bash
# tools/release-kit/release.sh — ONE parameterized releaser replacing seven
# per-repo copies that drifted (divoom-control, ztools, monitor, CadGoose,
# routines each grew their own; every one disagreed about ordering, gates and
# idempotency).
#
# Contract — steps run in this order, each skippable, every failure names its
# step:
#   0a. version   HEAD declares --version (`goh tag-version` over the tag that would be
#                 pushed, .gatesrc in its environment): never tag before the bump commit.
#   0. ci         every CI run for HEAD (`gh run list --commit`) complete and green (GitHub
#                 Actions DISABLED for the repo, read from its actions/permissions, is "no CI");
#                 HEAD not on origin/BRANCH is PUSHED first (the gated push) so CI runs on it,
#                 then watched: red refuses at once, pending or none at the deadline
#                 (GOH_RELEASE_CI_WAIT, default 1800 s), each run named (ztools tagged three
#                 releases over red CI). No workflows: "no CI", not a green. (ci_step.sh)
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
#                 (changelog_cut.py check; `cut` makes the stanza from Unreleased).
#   3. tag        create annotated tag vX.Y.Z if absent — idempotent: an
#                 existing tag is left alone, never moved or re-created
#   4. push       push branch + tag to origin (--no-push to skip)
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
# `../../gates` is nothing, and its parent already chose the tree (tests/test_release_kit.py).
[ -n "${GOH_RELEASE_BUFFERED:-}" ] \
  || { . "$(dirname "${BASH_SOURCE[0]}")/../../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"; }
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
if [ -z "${GOH_RELEASE_BUFFERED:-}" ]; then
  _SELF_COPY="$(mktemp "${TMPDIR:-/tmp}/goh-release-buffered.XXXXXX")"
  cat "$0" > "${_SELF_COPY}"
  export GOH_RELEASE_BUFFERED="${_SELF_COPY}"
  exec /bin/bash "${_SELF_COPY}" "$@"
fi
_SELF_COPY="${GOH_RELEASE_BUFFERED}"
unset GOH_RELEASE_BUFFERED

GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"
# shellcheck disable=SC1091
source "$GOH/tui/lib.sh"

VERSION=""
GATE_CMD="${GOH_RELEASE_GATE:-}"
BRANCH=""            # default: current branch, resolved later
CHANGELOG_CHECK=1
CI_CHECK=1
DO_PUSH=1
DO_RELEASE=1
DRY_RUN=0
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

# Regex-safe version for stanza matching (dots escaped). Every stanza helper
# takes the version as an argument: the release step also reads the stanzas
# of EARLIER tags it backfills, so none of them may hard-wire $VERSION.
ver_re() { printf '%s' "$1" | sed 's/[.*/\[\\]/\\&/g'; }
stanza_re() { # <version> -- ERE matching that version's CHANGELOG heading
  local r; r="$(ver_re "$1")"
  printf '%s' "^##+ v${r}[[:space:]]|^##+ v${r}\$|^##+ \\[${r}\\][[:space:]]|^##+ \\[${r}\\]\$"
}
has_stanza() { [ -f CHANGELOG.md ] && grep -Eq "$(stanza_re "$1")" CHANGELOG.md; }

stanza_body() { # <version>
  # No CHANGELOG.md (or --no-changelog-check on a repo without one) means an
  # empty body — the caller falls back to the tag name as the message. The
  # awk below must not run against a missing file: under set -e its failure
  # aborted the TAG step after the changelog step had already been skipped.
  [ -f CHANGELOG.md ] || return 0
  # The regex reaches awk through ENVIRON, NOT -v: -v processes backslash
  # escapes, so the escaped dots of v1\.2\.3 arrived as bare wildcards and
  # bogus headings (## v1x2y3, ## v19283) matched the stanza. ENVIRON is
  # POSIX and hands the regex over verbatim.
  # Body bounds: everything after the matching stanza's heading, up to the
  # NEXT heading AT OR ABOVE the stanza's own level. Keep-a-changelog bodies
  # carry ### subsections, so "###" must NOT end the body — only a heading
  # of the stanza's level (the next version) does. The old `/^##+ /` reset
  # collapsed every subsectioned body to nothing.
  GOH_AWK_VER_RE="$(ver_re "$1")" awk '
    BEGIN { pat = "^(##+) v" ENVIRON["GOH_AWK_VER_RE"] "( |$)|^(##+) \\[" ENVIRON["GOH_AWK_VER_RE"] "\\]( |$)" }
    $0 ~ pat {
      head = $0; sub(/[ \t].*$/, "", head); level = length(head)
      flag = 1; next
    }
    /^#/ {
      if (flag) {
        h = $0; sub(/[^#].*$/, "", h)
        if (length(h) <= level) { flag = 0; next }
      }
    }
    flag { print }
  ' CHANGELOG.md
}

# ── repo preconditions ───────────────────────────────────────────────────────
git rev-parse --show-toplevel >/dev/null 2>&1 \
  || die "precondition: not inside a git repository"
[ -f CHANGELOG.md ] || warn "no CHANGELOG.md in $(pwd) — the changelog step will fail"
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
[ -n "$ORIGIN_URL" ] || warn "no 'origin' remote — push and release steps will fail or skip"
BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
# github_slug URL -- `owner/name` when URL names a github.com repository, with or without `.git`,
# over https, ssh or scp syntax; nothing for any other URL. A sed that printed only when it stripped
# `.git` read a GitHub origin without it as no GitHub at all (no CI check, no release), and any other
# URL ending in `.git` -- a local bare repository, another host -- as a slug.
github_slug() {
  local rest
  case "$1" in
    https://github.com/* | http://github.com/* | https://*@github.com/* \
      | ssh://git@github.com/* | git@github.com:*) ;;
    *) return 0 ;;
  esac
  rest="${1#*github.com[:/]}"
  rest="${rest%/}"
  rest="${rest%.git}"
  case "$rest" in
    */*/* | /* | */) return 0 ;;
    ?*/?*) printf '%s\n' "$rest" ;;
  esac
}
# The URL as git uses it first; then as written, for an origin an `insteadOf` rewrites elsewhere.
GH_REPO_SLUG="$(github_slug "$ORIGIN_URL")"
[ -n "$GH_REPO_SLUG" ] \
  || GH_REPO_SLUG="$(github_slug "$(git config --get remote.origin.url 2>/dev/null || true)")"

if [ "$DRY_RUN" = 1 ]; then
  section "release plan for ${TAG} (dry-run — nothing will execute)"
else
  section "release ${TAG} from ${BRANCH}"
fi

# ── 0a. HEAD declares the version being tagged (tools/release-kit/version_step.sh) ──
# shellcheck source=tools/release-kit/version_step.sh
source "$GOH/tools/release-kit/version_step.sh"
release_version_step

# ── 0. CI on the commit being tagged ─────────────────────────────────────────
# First, because a red answer spares the gate and every step after it. An unpushed HEAD is pushed
# (gated) and its runs watched under a deadline: tools/release-kit/ci_step.sh.
# shellcheck source=tools/release-kit/ci_step.sh
source "$GOH/tools/release-kit/ci_step.sh"
release_ci_step "$CI_CHECK"

# ── 1. gate ──────────────────────────────────────────────────────────────────
begin "gate" "verify the local gate of record"
# A repo declares its release gate ONCE, in its .gatesrc (GOH_RELEASE_GATE), read by name in a child
# so nothing else in the file reaches this shell. Without it a bare release of goh itself was refused
# (GOH.SHX2). --gate and an exported GOH_RELEASE_GATE still beat it.
_top="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [ -z "$GATE_CMD" ] && [ -f "$_top/.gatesrc" ]; then
  GATE_CMD="$(cd "$_top" && bash -c 'unset GOH_RELEASE_GATE; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_RELEASE_GATE-}"')" \
    || fail "cannot read $_top/.gatesrc"
fi
release_backfill_floor  # GOH_RELEASE_BACKFILL_FLOOR -> BACKFILL_FLOOR (version_step.sh)
[ -n "$GATE_CMD" ] || fail "no gate configured — pass --gate CMD, set GOH_RELEASE_GATE, or declare GOH_RELEASE_GATE in .gatesrc"
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
    ARCHIVE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/goh-release-archive.XXXXXX")"
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
elif has_stanza "$VERSION"; then
  # The tag ships the whole tree, so at this commit nothing is unreleased: an Unreleased section
  # that still holds entries, or a stanza repeating an earlier one's entry, is refused (GOH.REL2:
  # goh's Unreleased carried twelve entries v0.60.0-v0.63.0 had shipped). changelog_cut.py cut
  # makes the stanza FROM Unreleased.
  python3 "$GOH/tools/release-kit/changelog_cut.py" check --version "$VERSION" --file CHANGELOG.md \
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
  BODY="$(mktemp "${TMPDIR:-/tmp}/goh-release-body.XXXXXX")"
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
  plan "git push origin ${BRANCH} ${TAG}"
else
  # ONE push: the pre-push gate refuses a branch that carries a local release tag the remote
  # lacks (goh tag-version --remote), which is what the first of two pushes always was.
  git push origin "$BRANCH" "$TAG" || fail "could not push ${BRANCH} + ${TAG}"
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
# earlier_versions: tag names or `ls-remote` lines on stdin → the X.Y[.Z] of
# each v-tag of the kit's scheme strictly before $VERSION, oldest first.
# Pre-release and other non-version tags (v1.2.3-rc1, nightly) never match.
# A repo whose CHANGELOG deliberately starts at some version (ztools: 56 tags before v2.2.0 that
# its preamble excludes) declares GOH_RELEASE_BACKFILL_FLOOR=vX.Y.Z in .gatesrc: tags below it are
# never backfilled nor warned about, in the live loop and the dry-run plan alike (BACKFILL_FLOOR).
earlier_versions() {
  GOH_AWK_VER="$VERSION" GOH_AWK_FLOOR="${BACKFILL_FLOOR#v}" awk '
    function key(v,  p, n) { n = split(v, p, "."); return sprintf("%09d%09d%09d", p[1], p[2], (n > 2 ? p[3] : 0)) }
    BEGIN { cur = key(ENVIRON["GOH_AWK_VER"]); floor = ENVIRON["GOH_AWK_FLOOR"]; low = (floor == "" ? "" : key(floor)) }
    { t = $NF; sub(/^refs\/tags\//, "", t) }
    t ~ /^v[0-9]+\.[0-9]+(\.[0-9]+)?$/ { v = substr(t, 2); k = key(v); if (k < cur && (low == "" || k >= low)) print k, v }
  ' | sort -u | cut -d' ' -f2
}

# A tag cut by hand never passed through this step, so its release was never
# created -- and nothing said so (ztools: three tags, "Latest" stuck at an
# older one). Every real release closes that gap for ALL earlier tags.
backfill_releases() {
  local versions v refs existing no_stanza="" made=0
  STEP="backfill"
  if [ "$DRY_RUN" = 1 ]; then
    versions="$(git tag -l 'v*' | earlier_versions)"
    for v in $versions; do
      has_stanza "$v" || continue
      plan "backfill: gh release create v${v} --latest=false --notes-file <v${v} stanza> \
-- only if origin has v${v} and no GitHub release of it yet"
    done
    STEP="release"; return 0
  fi
  refs="$(git ls-remote --tags --refs origin)" || fail "git ls-remote --tags origin exited nonzero"
  existing="$(gh release list --repo "$GH_REPO_SLUG" --limit 10000 --json tagName --jq '.[].tagName')" \
    || fail "gh release list exited nonzero"
  versions="$(earlier_versions <<<"$refs")"
  for v in $versions; do
    if grep -qxF "v${v}" <<<"$existing"; then continue; fi
    if ! has_stanza "$v"; then no_stanza="${no_stanza:+${no_stanza}, }v${v}"; continue; fi
    BACKFILL_NOTES="${BACKFILL_NOTES:-$(mktemp "${TMPDIR:-/tmp}/goh-release-backfill.XXXXXX")}"
    stanza_body "$v" > "$BACKFILL_NOTES"
    [ -s "$BACKFILL_NOTES" ] || printf '%s\n' "v${v}" > "$BACKFILL_NOTES"
    gh release create "v${v}" --title "v${v}" --notes-file "$BACKFILL_NOTES" \
      --latest=false --verify-tag --repo "$GH_REPO_SLUG" \
      || fail "gh release create v${v} exited nonzero"
    gh release view "v${v}" --repo "$GH_REPO_SLUG" >/dev/null 2>&1 \
      || fail "gh release create v${v} returned, but the release cannot be found -- not backfilled"
    ok "backfilled GitHub release v${v} (tag had none)"
    made=$((made + 1))
  done
  [ -z "$no_stanza" ] || warn "earlier tags on origin with no GitHub release and no CHANGELOG \
stanza, not backfilled: ${no_stanza} -- add a stanza and re-run, or release them by hand"
  [ "$made" -gt 0 ] || [ -n "$no_stanza" ] || info "backfill: every earlier tag on origin has a release"
  STEP="release"
}

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
  NOTES="$(mktemp "${TMPDIR:-/tmp}/goh-release-notes.XXXXXX")"
  stanza_body "$VERSION" > "$NOTES"
  if gh release view "$TAG" --repo "$GH_REPO_SLUG" >/dev/null 2>&1; then
    ok "GitHub release ${TAG} already exists — leaving it (idempotent)"
  else
    gh release create "$TAG" --title "$TAG" --notes-file "$NOTES" \
      --repo "$GH_REPO_SLUG" || fail "gh release create exited nonzero"
    gh release view "$TAG" --repo "$GH_REPO_SLUG" >/dev/null 2>&1 \
      || fail "gh release create returned, but GitHub release ${TAG} cannot be found -- not created"
    ok "created"
  fi
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
