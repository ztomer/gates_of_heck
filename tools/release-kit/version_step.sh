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
. "$(dirname "${BASH_SOURCE[0]}")/../../lib/bash5.sh" && goh_bash5 "$@"
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

# release_refusals_first (GOH.KITORDER): every refusal the kit can decide from the tree, asked
# BEFORE step 0b's cut commit and step 0's push. ztools 3.19.0 (2026-11-11) pushed its unpushed
# "chore: version" commit for the CI read, then refused at the changelog step: a pushed, untagged
# bump on origin. Asked here: a gate is declared (GATE_CMD, from .gatesrc as step 1 reads it), the
# backfill floor parses, an existing tag names HEAD, and -- when step 0b will cut nothing -- the
# stanza exists and `changelog_cut.py check` holds; a cut's own output is checked before it is
# committed (release_changelog_cut). Steps 1-3 keep their own reports. Tests:
# tests/test_release_kit_order.py.
release_refusals_first() {
  local _rtop
  _rtop="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  begin "gate" "a gate of record is declared (it runs at step 1)"
  if [ -z "$GATE_CMD" ] && [ -f "$_rtop/.gatesrc" ]; then
    GATE_CMD="$(cd "$_rtop" && bash -c 'unset GOH_RELEASE_GATE; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_RELEASE_GATE-}"')" \
      || fail "cannot read $_rtop/.gatesrc"
  fi
  [ -n "$GATE_CMD" ] || fail "no gate configured — pass --gate CMD, set GOH_RELEASE_GATE, or declare GOH_RELEASE_GATE in .gatesrc; nothing committed or pushed"
  release_backfill_floor  # GOH_RELEASE_BACKFILL_FLOOR -> BACKFILL_FLOOR
  begin "tag" "no ${TAG} names another commit"
  if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null \
      && [ "$(git rev-parse "refs/tags/${TAG}^{commit}")" != "$(git rev-parse HEAD)" ]; then
    fail "${TAG} exists at $(git rev-parse "refs/tags/${TAG}^{commit}") but HEAD is $(git rev-parse HEAD) — \
bump --version, or move the tag deliberately: git tag -f ${TAG} && git push -f origin ${TAG}; nothing committed or pushed"
  fi
  [ "$CHANGELOG_CHECK" = 1 ] || return 0
  begin "changelog" "CHANGELOG.md will hold a ${TAG} stanza, asked before the first commit or push"
  [ -f CHANGELOG.md ] || fail "no CHANGELOG.md, so no stanza for ${VERSION} (add one with '## ${TAG}', or --no-changelog-check); nothing committed or pushed"
  release_cut_pending && return 0  # step 0b cuts the stanza and checks it before committing
  has_stanza "$VERSION" || fail "CHANGELOG.md has no stanza for ${VERSION} and Unreleased holds nothing to cut (add '## ${TAG}' first, or entries under '## Unreleased'); nothing committed or pushed"
  bash "$GOH/gates/python.sh" "$GOH/tools/release-kit/changelog_cut.py" check --version "$VERSION" --file CHANGELOG.md \
    || fail "CHANGELOG.md does not hold for ${TAG} (above) -- run tools/release-kit/changelog_cut.py cut --version ${VERSION} --title ... and commit; nothing committed or pushed"
}

# release_cut_pending: 0 when step 0b will cut a stanza (Unreleased holds entries), else 1. A dry cut
# into a scratch copy, as step 0b's own; any other refusal of it is step 0b's to report.
release_cut_pending() {
  local _pcut _prc=0
  _pcut="$(mktemp "${TMPDIR:-/tmp}/goh-release-cutp.XXXXXX")"
  cp CHANGELOG.md "$_pcut" || { rm -f "$_pcut"; fail "cannot copy CHANGELOG.md"; }
  bash "$GOH/gates/python.sh" "$GOH/tools/release-kit/changelog_cut.py" cut --version "$VERSION" \
    ${TITLE:+--title "$TITLE"} --file "$_pcut" >/dev/null 2>"$_pcut.err" || _prc=1
  if [ "$_prc" = 1 ] && grep -q "Unreleased is empty\|no Unreleased section" "$_pcut.err"; then
    rm -f "$_pcut" "$_pcut.err"; return 1
  fi
  if [ "$_prc" = 0 ] && cmp -s CHANGELOG.md "$_pcut"; then  # a cut that changes nothing (GOH.CUTEMPTY)
    rm -f "$_pcut" "$_pcut.err"; return 1
  fi
  rm -f "$_pcut" "$_pcut.err"
  return 0
}

# release_changelog_cut (GOH.CUTAUTO): step 0b. v0.64.0 refused every release whose CHANGELOG still
# held committed Unreleased entries and told the human to run changelog_cut.py -- divoom-control,
# token-pool and ztools all held some, so each one's next release would have been refused for a step
# the kit can do itself. With no stanza for VERSION and entries under Unreleased, the kit cuts the
# stanza FROM Unreleased and commits it: amended INTO HEAD when HEAD is the wrappers' unpushed
# "chore: version V" commit (so the version commit carries its own stanza), else as its own
# "chore: changelog vV" commit. It runs before CI and the gate, so both see the tree that is tagged.
# A cut that would repeat an earlier stanza's entry is refused by changelog_cut.py, nothing written.
release_changelog_cut() {
  local _ccut _csubj
  [ "$CHANGELOG_CHECK" = 1 ] && [ -f CHANGELOG.md ] || return 0
  _ccut="$(mktemp "${TMPDIR:-/tmp}/goh-release-cut.XXXXXX")"
  cp CHANGELOG.md "$_ccut" || fail "cannot copy CHANGELOG.md"
  if ! bash "$GOH/gates/python.sh" "$GOH/tools/release-kit/changelog_cut.py" cut --version "$VERSION" \
      ${TITLE:+--title "$TITLE"} --file "$_ccut" >/dev/null 2>"$_ccut.err"; then
    if grep -q "Unreleased is empty\|no Unreleased section" "$_ccut.err"; then
      rm -f "$_ccut" "$_ccut.err"
      return 0  # nothing to cut: the changelog step names the missing stanza
    fi
    begin "changelog-cut" "make the ${TAG} stanza from Unreleased"
    cat "$_ccut.err" >&2
    rm -f "$_ccut" "$_ccut.err"
    fail "the ${TAG} stanza cannot be cut from Unreleased (above) -- nothing written, pushed or tagged"
  fi
  rm -f "$_ccut.err"
  # The stanza is written and Unreleased is empty: the cut changed nothing, so nothing is committed
  # (GOH.CUTEMPTY: committing it exited nonzero with nothing staged); the changelog step checks it.
  if cmp -s CHANGELOG.md "$_ccut"; then rm -f "$_ccut"; return 0; fi
  begin "changelog-cut" "make the ${TAG} stanza from Unreleased"
  # The cut is checked BEFORE it is committed (GOH.KITORDER): a refusal leaves HEAD as it was.
  bash "$GOH/gates/python.sh" "$GOH/tools/release-kit/changelog_cut.py" check --version "$VERSION" --file "$_ccut" \
    || { rm -f "$_ccut"; fail "the cut ${TAG} stanza does not hold (above) -- nothing written, committed or pushed"; }
  _csubj="$(git log -1 --format=%s)"
  if [ "$DRY_RUN" = 1 ]; then
    rm -f "$_ccut"
    plan "changelog_cut.py cut --version ${VERSION}, committed into HEAD ('${_csubj}') or as 'chore: changelog ${TAG}'"
    return 3  # the caller notes a PLANNED stanza, which the dry run's changelog step stands on
  fi
  mv "$_ccut" CHANGELOG.md || fail "cannot write CHANGELOG.md"
  git add CHANGELOG.md || fail "git add CHANGELOG.md exited nonzero"
  if [ "$_csubj" = "chore: version ${VERSION}" ] && [ -z "$(git branch -r --contains HEAD 2>/dev/null)" ]; then
    git commit -q --amend --no-edit || fail "amending the stanza into '${_csubj}' exited nonzero"
    ok "${TAG} stanza cut from Unreleased into $(git rev-parse --short HEAD) ('${_csubj}')"
  else
    git commit -q -m "chore: changelog ${TAG}" || fail "committing the ${TAG} stanza exited nonzero"
    ok "${TAG} stanza cut from Unreleased as $(git rev-parse --short HEAD)"
  fi
}
