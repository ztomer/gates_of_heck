#!/usr/bin/env bash
# tools/release-kit/release.sh — ONE parameterized releaser replacing seven
# per-repo copies that drifted (divoom-control, ztools, monitor, CadGoose,
# routines each grew their own; every one disagreed about ordering, gates and
# idempotency).
#
# Contract — steps run in this order, each skippable, every failure names its
# step:
#   1. gate       verify the local gate-of-record is green
#                 (--gate CMD or GOH_RELEASE_GATE; a release is only cut on a
#                 green gate of record)
#   2. changelog  verify CHANGELOG.md has a stanza headed by --version
#                 (## vX.Y.Z or ## [X.Y.Z]); --no-changelog-check to skip.
#                 The stanza body becomes the tag message and release notes,
#                 so docs and releases cannot disagree.
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
  _SELF_COPY="$(mktemp "${TMPDIR:-/tmp}/release-buffered.XXXXXX")"
  cat "$0" > "${_SELF_COPY}"
  export GOH_RELEASE_BUFFERED="${_SELF_COPY}"
  exec /bin/bash "${_SELF_COPY}" "$@"
fi
_SELF_COPY="${GOH_RELEASE_BUFFERED}"

GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"
# shellcheck disable=SC1091
source "$GOH/tui/lib.sh"

VERSION=""
GATE_CMD="${GOH_RELEASE_GATE:-}"
BRANCH=""            # default: current branch, resolved later
CHANGELOG_CHECK=1
DO_PUSH=1
DO_RELEASE=1
DRY_RUN=0
TAP=""
TAP_KIND=""          # formula | cask
TAP_NAME=""
ARTIFACT=""

die_usage() { err "usage: $0 --version X.Y.Z [--gate CMD] [--dry-run] ...  (see header)"; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version)            VERSION="${2:?}"; shift 2 ;;
    --gate)               GATE_CMD="${2:?}"; shift 2 ;;
    --branch)             BRANCH="${2:?}"; shift 2 ;;
    --no-changelog-check) CHANGELOG_CHECK=0; shift ;;
    --no-push)            DO_PUSH=0; shift ;;
    --skip-release)       DO_RELEASE=0; shift ;;
    --dry-run)            DRY_RUN=1; shift ;;
    --tap)                TAP="${2:?}"; shift 2 ;;
    --formula)            TAP_KIND="formula"; TAP_NAME="${2:?}"; shift 2 ;;
    --cask)               TAP_KIND="cask"; TAP_NAME="${2:?}"; shift 2 ;;
    --artifact)           ARTIFACT="${2:?}"; shift 2 ;;
    -h|--help)            sed -n '2,45p' "$0"; exit 0 ;;
    *) die_usage ;;
  esac
done
[ -n "$VERSION" ] || die_usage
# X.Y.Z (semver) or X.Y — some repos' tag scheme is two-component (CadGoose:
# v1.71 … v1.79); the old three-glob check rejected their entire history.
if printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+(\.[0-9]+)?$'; then :; else
  die "step 'args': --version must look like 1.2.3 or 1.79 (got '$VERSION')"
fi
if [ -n "$TAP" ] && [ -z "$TAP_NAME" ]; then
  die "step 'args': --tap needs --formula NAME or --cask NAME"
fi
if [ -n "$TAP_NAME" ] && [ -z "$TAP" ]; then
  die "step 'args': --formula/--cask need --tap ORG/REPO"
fi

TAG="v${VERSION}"
BODY="" NOTES="" TAP_DIR=""
cleanup() {
  # Every branch must succeed — this trap's status becomes the script's.
  [ -z "$BODY" ] || rm -f "$BODY"
  [ -z "$NOTES" ] || rm -f "$NOTES"
  [ -z "$TAP_DIR" ] || rm -rf "$TAP_DIR"
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

# Regex-safe version for stanza matching (dots escaped).
VER_RE="$(printf '%s' "$VERSION" | sed 's/[.*/\[\\]/\\&/g')"
STANZA_RE="^##+ v${VER_RE}[[:space:]]|^##+ v${VER_RE}\$|^##+ \\[${VER_RE}\\][[:space:]]|^##+ \\[${VER_RE}\\]\$"

stanza_body() {
  # No CHANGELOG.md (or --no-changelog-check on a repo without one) means an
  # empty body — the caller falls back to the tag name as the message. The
  # awk below must not run against a missing file: under set -e its failure
  # aborted the TAG step after the changelog step had already been skipped.
  [ -f CHANGELOG.md ] || return 0
  # Body bounds: everything after the matching stanza's heading, up to the
  # NEXT heading AT OR ABOVE the stanza's own level. Keep-a-changelog bodies
  # carry ### subsections, so "###" must NOT end the body — only a heading
  # of the stanza's level (the next version) does. The old `/^##+ /` reset
  # collapsed every subsectioned body to nothing.
  awk -v pat="^(##+) v${VER_RE}( |\$)|^(##+) \\[${VER_RE}\\]( |\$)" '
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
GH_REPO_SLUG="$(printf '%s' "$ORIGIN_URL" | sed -n 's#.*github.com[:/]##; s#\.git$##p')"

if [ "$DRY_RUN" = 1 ]; then
  section "release plan for ${TAG} (dry-run — nothing will execute)"
else
  section "release ${TAG} from ${BRANCH}"
fi

# ── 1. gate ──────────────────────────────────────────────────────────────────
begin "gate" "verify the local gate of record"
[ -n "$GATE_CMD" ] || fail "no gate configured — pass --gate CMD or set GOH_RELEASE_GATE"
if [ "$DRY_RUN" = 1 ]; then
  plan "run gate: ${GATE_CMD}"
else
  bash -c "$GATE_CMD" || fail "gate command exited nonzero (${GATE_CMD})"
  ok "gate green"
fi

# ── 2. changelog stanza ──────────────────────────────────────────────────────
begin "changelog" "look for a ${TAG} stanza in CHANGELOG.md"
if [ "$CHANGELOG_CHECK" = 0 ]; then
  warn "skipped (--no-changelog-check)"
elif grep -Eq "$STANZA_RE" CHANGELOG.md; then
  ok "found"
else
  fail "CHANGELOG.md has no stanza for ${VERSION} (add '## ${TAG}' first)"
fi

# ── 3. annotated tag (idempotent) ────────────────────────────────────────────
begin "tag" "annotated tag ${TAG}"
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  ok "${TAG} already exists — leaving it (idempotent)"
elif [ "$DRY_RUN" = 1 ]; then
  plan "git tag -a ${TAG} at $(git rev-parse --short HEAD)"
else
  BODY="$(mktemp)"
  stanza_body > "$BODY"
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
  git push origin "$BRANCH" || fail "could not push ${BRANCH}"
  git push origin "$TAG" || fail "could not push ${TAG}"
  ok "pushed"
fi

# ── 5. GitHub release ────────────────────────────────────────────────────────
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
  plan "gh release create ${TAG} --notes-file <stanza body> (repo ${GH_REPO_SLUG})"
else
  NOTES="$(mktemp)"
  stanza_body > "$NOTES"
  if gh release view "$TAG" --repo "$GH_REPO_SLUG" >/dev/null 2>&1; then
    ok "GitHub release ${TAG} already exists — leaving it (idempotent)"
  else
    gh release create "$TAG" --title "$TAG" --notes-file "$NOTES" \
      --repo "$GH_REPO_SLUG" || fail "gh release create exited nonzero"
    ok "created"
  fi
fi

# ── 6. Homebrew tap bump ─────────────────────────────────────────────────────
if [ -z "$TAP_NAME" ]; then
  info "tap: not requested — skipping"
else
  begin "tap" "${TAP_KIND} ${TAP_NAME} in ${TAP}"
  TAP_SUBDIR="Formula"; [ "$TAP_KIND" = "cask" ] && TAP_SUBDIR="Casks"

  if [ "$DRY_RUN" = 1 ]; then
    plan "compute sha256 of artifact, rewrite url+sha256 in ${TAP_SUBDIR}/${TAP_NAME}.rb, commit + push"
  else
    if [ -d "$TAP" ]; then
      TAP_SRC="$TAP"          # local path (tests, private remotes)
    elif printf '%s' "$TAP" | grep -q '/'; then
      TAP_SRC="https://github.com/${TAP}"
    else
      fail "--tap must be ORG/REPO or a local git path (got '${TAP}')"
    fi
    TAP_DIR="$(mktemp -d)"
    git clone -q "$TAP_SRC" "$TAP_DIR/tap" || fail "could not clone ${TAP}"
    FORMULA_FILE="$TAP_DIR/tap/${TAP_SUBDIR}/${TAP_NAME}.rb"
    [ -f "$FORMULA_FILE" ] || fail "not found: ${TAP_SUBDIR}/${TAP_NAME}.rb in ${TAP}"

    # Artifact digest: a local file is hashed directly; a URL is streamed.
    if [ -z "$ARTIFACT" ]; then
      ARTIFACT="https://github.com/${GH_REPO_SLUG}/archive/refs/tags/${TAG}.tar.gz"
      [ -n "$GH_REPO_SLUG" ] || fail "no --artifact given and origin is not github.com — cannot derive tarball URL"
    fi
    case "$ARTIFACT" in
      http://|https://*) SHA="$(curl -fsSL "$ARTIFACT" | shasum -a 256 | cut -d' ' -f1)" ;;
      *)                 [ -f "$ARTIFACT" ] || fail "artifact not found: ${ARTIFACT}"
                         SHA="$(shasum -a 256 "$ARTIFACT" | cut -d' ' -f1)" ;;
    esac
    [ -n "$SHA" ] || fail "could not compute sha256 of ${ARTIFACT}"

    NEW_TAG_RE="$(printf '%s' "$TAG" | sed 's/[.*/\[\\]/\\&/g')"
    sed -i '' \
      -e "s#/v${NEW_TAG_RE}\.tar\.gz#/v${NEW_TAG_RE}.tar.gz#g; s#/tags/v[0-9][0-9.]*\.tar\.gz#/tags/${TAG}.tar.gz#" \
      -e "s/sha256 \"[0-9a-fA-F]*\"/sha256 \"${SHA}\"/" \
      "$FORMULA_FILE"
    grep -q "sha256 \"${SHA}\"" "$FORMULA_FILE" \
      || fail "sha256 line unchanged after sed — unexpected ${TAP_KIND} format"

    if git -C "$TAP_DIR/tap" diff --quiet; then
      ok "already at ${TAG} with this sha256 — nothing to commit (idempotent)"
    else
      GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-$(git config user.name)}" \
      GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-$(git config user.email)}" \
      GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$(git config user.name)}" \
      GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$(git config user.email)}" \
        git -C "$TAP_DIR/tap" commit -aqm "${TAP_NAME} ${TAG}" \
        || fail "tap commit failed"
      git -C "$TAP_DIR/tap" push -q origin HEAD || fail "tap push failed"
      ok "committed + pushed ${TAP}/${TAP_SUBDIR}/${TAP_NAME}.rb → ${TAG}"
    fi
  fi
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
