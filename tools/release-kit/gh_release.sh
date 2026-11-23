# shellcheck shell=bash
# tools/release-kit/gh_release.sh -- the CHANGELOG stanza readers, github_slug, and the GitHub
# release step (backfill every earlier release-less tag, then cut this one, each verified), shared by
# release.sh and goh's own kit tools/release_goh.sh (GOH.SELFGHRELEASE: goh's kit made no GitHub
# release from v0.96.0 on, the very state this backfill closes in every consumer). Sourced, never
# run. The caller provides the kit's globals (VERSION, DRY_RUN, BACKFILL_FLOOR, GH_REPO_SLUG,
# BACKFILL_NOTES, STEP) and helpers (plan ok info warn fail), and runs from the repo root, where
# CHANGELOG.md is read. Tests: tests/test_release_kit.py, tests/test_release_goh.py.
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "tools/release-kit/gh_release.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../../lib/bash5.sh" || exit; goh_bash5 "$@"
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
    BACKFILL_NOTES="${BACKFILL_NOTES:-$(mktemp "$(goh_tmp)/goh-release-backfill.XXXXXX")}"
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
  # shellcheck disable=SC2034  # STEP is the caller's, read by its fail()
  STEP="release"
}

# cut_release: the GitHub release of $TAG (= v$VERSION) from its stanza, created unless it already
# exists (idempotent), then read back: a create that returned 0 but left no release is a failure.
# The notes file is NOTES, which the caller's cleanup removes.
cut_release() {
  NOTES="$(mktemp "$(goh_tmp)/goh-release-notes.XXXXXX")"
  stanza_body "$VERSION" > "$NOTES"
  [ -s "$NOTES" ] || printf '%s\n' "$TAG" > "$NOTES"
  if gh release view "$TAG" --repo "$GH_REPO_SLUG" >/dev/null 2>&1; then
    ok "GitHub release ${TAG} already exists — leaving it (idempotent)"
  else
    gh release create "$TAG" --title "$TAG" --notes-file "$NOTES" --verify-tag \
      --repo "$GH_REPO_SLUG" || fail "gh release create exited nonzero"
    gh release view "$TAG" --repo "$GH_REPO_SLUG" >/dev/null 2>&1 \
      || fail "gh release create returned, but GitHub release ${TAG} cannot be found -- not created"
    ok "created"
  fi
}
