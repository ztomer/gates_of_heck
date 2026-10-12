#!/usr/bin/env bash
# tools/release-kit/tap_bump.sh -- the house Homebrew tap bump, through the TAP's own gate (BACKLOG 6.8).
#
# Five products' release scripts pushed homebrew-tap bumps straight to GitHub, so the tap's gate
# never ran: a fresh clone has no core.hooksPath, so no hook fires, and a bump pushed from CI with
# a token skips hooks by construction. This step does not rely on a hook. It clones the tap,
# rewrites the url tag and sha256 of Formula/NAME.rb or Casks/NAME.rb, commits, runs the tap's own
# `tools/gate.sh --full` in the clone, and pushes the commit PINNED (`<sha>:refs/heads/<branch>`),
# reading the remote back. A red gate pushes nothing. An unchanged formula commits nothing.
#
#   tap_bump.sh --tap ORG/REPO|PATH (--formula NAME | --cask NAME) --tag vX.Y.Z --artifact PATH|URL
#               [--dry-run]
#
# release.sh's tap step calls this; a product's own release script calls it the same way.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"
# shellcheck disable=SC1091
source "$GOH/tui/lib.sh"

TAP="" KIND="" NAME="" TAG="" ARTIFACT="" DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tap)      TAP="${2:?}"; shift 2 ;;
    --formula)  KIND="formula"; NAME="${2:?}"; shift 2 ;;
    --cask)     KIND="cask"; NAME="${2:?}"; shift 2 ;;
    --tag)      TAG="${2:?}"; shift 2 ;;
    --artifact) ARTIFACT="${2:?}"; shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  sed -n '2,14p' "$0"; exit 0 ;;
    *) die "tap_bump: unknown argument '$1' (see --help)" ;;
  esac
done
[ -n "$TAP" ] && [ -n "$NAME" ] && [ -n "$TAG" ] && [ -n "$ARTIFACT" ] \
  || die "tap_bump: --tap, --formula/--cask, --tag and --artifact are all required"
SUBDIR="Formula"; [ "$KIND" = cask ] && SUBDIR="Casks"

if [ "$DRY_RUN" = 1 ]; then
  info "[dry-run] sha256 of ${ARTIFACT}; rewrite ${SUBDIR}/${NAME}.rb to ${TAG}; the tap's gate; pinned push"
  exit 0
fi

if [ -d "$TAP" ]; then
  src="$TAP"          # a local path (tests, private remotes)
elif grep -q '/' <<<"$TAP"; then
  src="https://github.com/${TAP}"
else
  die "tap_bump: --tap must be ORG/REPO or a local git path (got '${TAP}')"
fi
work="$(mktemp -d "${TMPDIR:-/tmp}/goh-tap-bump.XXXXXX")"
trap 'rm -rf "$work"' EXIT
git clone -q "$src" "$work/tap" || die "tap_bump: could not clone ${TAP}"
file="$work/tap/${SUBDIR}/${NAME}.rb"
[ -f "$file" ] || die "tap_bump: not found: ${SUBDIR}/${NAME}.rb in ${TAP}"

# The digest of a URL is taken from a downloaded file, curl's failure named: a bare
# `curl | shasum` under set -e hashed EMPTY input and handed the tap the digest of nothing.
case "$ARTIFACT" in
  http://*|https://*)
    curl -fsSL "$ARTIFACT" -o "$work/artifact" || die "tap_bump: could not download artifact: ${ARTIFACT}"
    sha="$(shasum -a 256 "$work/artifact" | cut -d' ' -f1)" ;;
  *)
    [ -f "$ARTIFACT" ] || die "tap_bump: artifact not found: ${ARTIFACT}"
    sha="$(shasum -a 256 "$ARTIFACT" | cut -d' ' -f1)" ;;
esac

tag_re="$(printf '%s' "$TAG" | sed 's/[.*/\[\\]/\\&/g')"
sed -i '' \
  -e "s#/${tag_re}\.tar\.gz#/${tag_re}.tar.gz#g; s#/tags/v[0-9][0-9.]*\.tar\.gz#/tags/${TAG}.tar.gz#" \
  -e "s/sha256 \"[0-9a-fA-F]*\"/sha256 \"${sha}\"/" \
  "$file"
grep -q "sha256 \"${sha}\"" "$file" || die "tap_bump: the sha256 line did not change -- unexpected ${KIND} format"

if git -C "$work/tap" diff --quiet; then
  ok "${NAME} is already at ${TAG} with this sha256 -- nothing to commit"
  exit 0
fi
GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-$(git config user.name)}" \
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-$(git config user.email)}" \
GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$(git config user.name)}" \
GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$(git config user.email)}" \
  git -C "$work/tap" commit -aqm "${NAME} ${TAG}" || die "tap_bump: the commit failed"

# THE TAP'S GATE, run here rather than trusted to a hook that a fresh clone never installs.
if [ -f "$work/tap/tools/gate.sh" ]; then
  (cd "$work/tap" && GOH_DIR="$GOH" bash tools/gate.sh --full) \
    || die "tap_bump: the tap's own gate refused ${NAME} ${TAG} (above) -- nothing pushed"
  ok "the tap's gate passed"
else
  warn "the tap has no tools/gate.sh -- nothing judged this bump before the push"
fi

commit="$(git -C "$work/tap" rev-parse HEAD)"
branch="$(git -C "$work/tap" rev-parse --abbrev-ref HEAD)"
git -C "$work/tap" push -q origin "${commit}:refs/heads/${branch}" || die "tap_bump: the push was refused"
have="$(git -C "$work/tap" ls-remote origin "refs/heads/${branch}" | cut -f1)"
[ "$have" = "$commit" ] || die "tap_bump: origin/${branch} is ${have:-missing}, not ${commit:0:12}, after a push that returned 0"
ok "pushed ${TAP}/${SUBDIR}/${NAME}.rb -> ${TAG} (${commit:0:12})"
exit
} # parse-guard
