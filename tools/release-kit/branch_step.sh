# shellcheck shell=bash
# tools/release-kit/branch_step.sh -- which branch of origin the release goes to (GOH.RELBRANCH).
# Sourced by release.sh (from $GOH), never run; it reads ORIGIN_URL DRY_RUN DO_PUSH and the --branch
# value in BRANCH, and sets BRANCH (the branch of ORIGIN the release commit lands on) and
# PUSH_REFSPEC (what `git push origin` is given for it).
#
# The release goes to origin's DEFAULT branch, whatever branch it is cut from. It used to push the
# CURRENT branch: cut from a tools/land.sh worktree it created `goh-loadlock` on origin and left
# main behind (2026-11, goh v0.69.0). Now:
#
#   on the default branch   `git push origin <default>`, as it always was;
#   on any other branch     `git push origin HEAD:refs/heads/<default>`, and only when that
#                           fast-forwards origin's default branch (HEAD descends from it) --
#                           else a refusal, before the gate and the tag, naming the way forward;
#   --branch NAME           NAME is pushed as itself (the caller named a branch on purpose).
#
# The default is origin's HEAD: the local origin/HEAD when set, else `git ls-remote --symref`
# (a dry run has no network: an unset origin/HEAD reads main or master when the last fetch saw it or HEAD is on it, else it
# is planned as "origin's default branch").
# Tests: tests/test_release_kit_branch.py.

# release_default_branch -- origin's default branch name, or nothing when it cannot be read.
release_default_branch() {
  local def
  def="$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  def="${def#origin/}"
  if [ -z "$def" ] && [ "$DRY_RUN" = 0 ] && [ -n "$ORIGIN_URL" ]; then
    def="$(git ls-remote --symref origin HEAD 2>/dev/null \
      | awk '$1 == "ref:" && !seen { sub("^refs/heads/", "", $2); print $2; seen = 1 }' || true)"
  fi
  if [ -z "$def" ] && [ "$DRY_RUN" = 1 ]; then   # no network: a conventional name origin was seen with, or HEAD is on
    for def in main master; do
      git rev-parse -q --verify "refs/remotes/origin/${def}" >/dev/null && break
      [ "$(git rev-parse --abbrev-ref HEAD)" = "$def" ] && break
      def=""
    done
  fi
  printf '%s' "$def"
}

# push_to REFSPEC -- sets PUSH_REFSPEC, the global release.sh and ci_step.sh read (exported: sourced code, one file apart).
push_to() { PUSH_REFSPEC="$1"; export PUSH_REFSPEC; }

# release_branch_step -- BRANCH and PUSH_REFSPEC, or a refusal naming the way forward.
release_branch_step() {
  local current def remote_head
  begin "branch" "the branch of origin the release goes to"
  push_to ""
  if [ -n "$BRANCH" ]; then push_to "$BRANCH"; return 0; fi
  current="$(git rev-parse --abbrev-ref HEAD)"
  def="$(release_default_branch)"
  if [ -z "$def" ]; then
    if [ "$DRY_RUN" = 1 ]; then BRANCH="origin's default branch"; push_to "HEAD:refs/heads/<default>"; return 0; fi
    [ "$current" != HEAD ] || fail "HEAD is detached and origin's default branch cannot be read -- pass --branch NAME"
    BRANCH="$current"; push_to "$current"
    [ -z "$ORIGIN_URL" ] || warn "origin's default branch cannot be read: releasing to ${current}"
    return 0
  fi
  BRANCH="$def"
  if [ "$current" = "$def" ]; then push_to "$def"; return 0; fi
  push_to "HEAD:refs/heads/${def}"
  [ "$DRY_RUN" = 0 ] && [ "$DO_PUSH" = 1 ] || return 0
  remote_head="$(git ls-remote origin "refs/heads/${def}" | cut -f1)" || fail "git ls-remote origin exited nonzero"
  [ -n "$remote_head" ] || return 0
  git merge-base --is-ancestor "$remote_head" HEAD 2>/dev/null \
    || fail "releasing from ${current}, which would update origin's ${def} (${remote_head:0:12}), but HEAD does not descend \
from it -- rebase ${current} onto origin/${def} (git fetch; tools/land.sh in a worktree) and release again; nothing pushed"
}
