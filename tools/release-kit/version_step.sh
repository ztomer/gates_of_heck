# shellcheck shell=bash
# tools/release-kit/version_step.sh -- step 0a of release.sh, and the backfill floor it reads.
# Sourced by release.sh (from $GOH, the HEAD export), never run; it reads the kit's globals
# (VERSION TAG GOH) and its helpers (begin ok fail). Tests: tests/test_release_kit_version.py.
#
# release_version_step:
# The tag names HEAD, so HEAD must already carry the version: goh's v0.63.0 was tagged on the
# changelog commit, BEFORE the commit that bumped Cargo.toml, and only the pre-push tag check refused
# it -- after the tag existed, so it was deleted and re-cut by hand. The same check (`goh tag-version`,
# with the repo's .gatesrc in its environment as the pre-push gives it) is asked here, first, over the
# tag that WOULD be pushed: a release whose version is not committed refuses before anything is
# pushed, waited for or tagged (tests/test_release_kit_version.py).
release_version_step() {
  local _vtop _vrefs _vrc
  begin "version" "HEAD declares ${VERSION}"
  _vtop="$(git rev-parse --show-toplevel)"
  _vrefs="$(mktemp "${TMPDIR:-/tmp}/goh-release-refs.XXXXXX")"
  printf 'refs/tags/%s %s refs/tags/%s 0000000000000000000000000000000000000000\n' \
    "$TAG" "$(git rev-parse HEAD)" "$TAG" > "$_vrefs"
  _vrc=0
  (
    if [ -f "$_vtop/.gatesrc" ]; then
      set -a
      # shellcheck source=/dev/null
      . "$_vtop/.gatesrc" >/dev/null 2>&1 </dev/null
      set +a
    fi
    bash "$GOH/gates/goh.sh" tag-version --root "$_vtop" --refs-file "$_vrefs" >/dev/null  # findings: stderr
  ) || _vrc=$?
  rm -f "$_vrefs"
  if [ "$_vrc" -eq 1 ]; then
    fail "HEAD $(git rev-parse --short HEAD) does not declare ${VERSION} (above) -- commit the version bump first, then release; nothing pushed or tagged"
  elif [ "$_vrc" -ne 0 ]; then
    fail "the version check could not run (exit ${_vrc}, above) -- nothing pushed or tagged"
  fi
  ok "HEAD declares ${VERSION}"
}

# release_backfill_floor: sets BACKFILL_FLOOR from GOH_RELEASE_BACKFILL_FLOOR, read from .gatesrc by
# name in a child like GOH_RELEASE_GATE; an exported value beats it. A non-version is refused.
release_backfill_floor() {
  local top
  top="$(git rev-parse --show-toplevel)"
  BACKFILL_FLOOR="${GOH_RELEASE_BACKFILL_FLOOR:-}"
  if [ -z "$BACKFILL_FLOOR" ] && [ -f "$top/.gatesrc" ]; then
    BACKFILL_FLOOR="$(cd "$top" && bash -c 'unset GOH_RELEASE_BACKFILL_FLOOR; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_RELEASE_BACKFILL_FLOOR-}"')" \
      || fail "cannot read $top/.gatesrc"
  fi
  if [ -n "$BACKFILL_FLOOR" ] && ! grep -Eq '^v?[0-9]+\.[0-9]+(\.[0-9]+)?$' <<<"$BACKFILL_FLOOR"; then
    fail "GOH_RELEASE_BACKFILL_FLOOR must look like v1.2.3 or 1.2 (got '$BACKFILL_FLOOR')"
  fi
}
