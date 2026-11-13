# shellcheck shell=bash
# tools/release-kit/ci_step.sh -- step 0 of release.sh: the CI runs for the commit being tagged.
# Sourced by release.sh (from $GOH, the HEAD export), never run; it reads the kit's globals
# (DRY_RUN DO_PUSH BRANCH GH_REPO_SLUG ORIGIN_URL) and its helpers (begin plan fail).
#
# The order is push -> read -> tag, in the kit, never by hand (GOH.RELCI). A wrapper commits
# "chore: version X" and calls the kit with that commit unpushed; GitHub had no run for it, so this
# step refused "no CI run for HEAD" (ztools v3.12.2, 2026-11-07). Now, when origin/<branch> is not
# HEAD, the branch is pushed through the NORMAL push (its pre-push gate runs; never --no-verify) and
# the runs for HEAD are read ONCE, never waited on: hosted CI re-runs the gate the push already
# passed, so it is ADVISORY (owner ruling 2026-11-11; .loop LOOP.CIADVISORY; GOH.CIWAIT -- this step
# used to watch for up to 1800 s):
#
#   a red run      refuses, naming it: a finding, not capacity;
#   all green      tags;
#   pending / none tags ADVISORY when the checkout's pre-push hook is goh's push gate (the local
#                  record that HEAD passed the full gate; `loopctl tick` re-reads the run and names a
#                  red one); with no push-gate hook it refuses at once -- nothing local says HEAD was
#                  gated -- naming the re-run and --no-ci-check.
#
# --no-push with an unpushed HEAD refuses: no CI can run on a commit origin does not have.
# Tests: tests/test_release_kit_ci.py (the fake gh lists runs only for commits origin holds).

. "$(dirname "${BASH_SOURCE[0]}")/../../lib/bash5.sh" && goh_bash5 "$@"

# ci_push_gated -- the checkout's pre-push hook (as git resolves it: core.hooksPath or the common
# hooks dir) when it runs goh's push gate, else nothing (.loop tools/loopadvisory.py push_gated).
ci_push_gated() {
  local hook
  hook="$(git rev-parse --git-path hooks/pre-push 2>/dev/null)" || return 0
  [ -f "$hook" ] && grep -q 'push_gate\.sh' "$hook" 2>/dev/null && printf '%s' "$hook"
  return 0
}

# ci_runs -- the runs for HEAD_SHA, one `status<TAB>conclusion<TAB>name<TAB>url` line each.
ci_runs() {
  gh run list --repo "$GH_REPO_SLUG" --commit "$HEAD_SHA" --limit 100 \
    --json status,conclusion,name,url \
    --jq '.[] | [.status, (.conclusion // ""), .name, .url] | @tsv'
}

# ci_name_runs <runs> -- one err line per run. awk, not `read`: a tab is IFS whitespace, so `read`
# merges a pending run's empty conclusion field.
ci_name_runs() {
  local line
  while IFS= read -r line; do err "$line"; done < <(awk -F'\t' \
    '{ printf "  %s: %s -- %s\n", $3, ($2 == "" ? $1 : $2), $4 }' <<<"$1")
}

# ci_push_head -- origin/<branch> made HEAD, through the gated push, or a refusal naming why not.
ci_push_head() {
  local remote_head
  remote_head="$(git ls-remote origin "refs/heads/${BRANCH}" | cut -f1)" \
    || fail "git ls-remote origin exited nonzero"
  [ "$remote_head" != "$HEAD_SHA" ] || return 0
  [ "$DO_PUSH" = 1 ] || fail "HEAD (${HEAD_SHA:0:12}) is not on origin/${BRANCH} \
(${remote_head:-missing}) and --no-push forbids pushing it, so no CI can run on it -- push it \
first, or --no-ci-check"
  info "HEAD ${HEAD_SHA:0:12} is not on origin/${BRANCH}: pushing it so CI runs on it"
  git push origin "$PUSH_REFSPEC" || fail "could not push ${BRANCH} for its CI run"
  remote_head="$(git ls-remote origin "refs/heads/${BRANCH}" | cut -f1)" \
    || fail "git ls-remote origin exited nonzero"
  [ "$remote_head" = "$HEAD_SHA" ] || fail "origin/${BRANCH} is ${remote_head:-missing}, not \
${HEAD_SHA:0:12}, after a push that returned 0 -- not pushed"
  ok "pushed ${BRANCH} @ ${HEAD_SHA:0:12}"
}

# ci_read -- read the runs for HEAD once: red refuses, green tags, pending or none tags ADVISORY over
# goh's push gate and refuses at once without one. Never waits (GOH.CIWAIT).
ci_read() {
  local runs red pending hook line state
  runs="$(ci_runs)" || fail "gh run list could not read the CI runs for ${HEAD_SHA:0:12}"
  red="$(awk -F'\t' '$1 == "completed" && $2 != "success" && $2 != "skipped" && $2 != "neutral"' <<<"$runs")"
  if [ -n "$red" ]; then
    ci_name_runs "$red"
    fail "CI on HEAD (${HEAD_SHA:0:12}) is not green (above) -- nothing tagged"
  fi
  pending="$(awk -F'\t' 'NF && $1 != "completed"' <<<"$runs")"
  if [ -n "$runs" ] && [ -z "$pending" ]; then
    ok "$(counted "$(wc -l <<<"$runs")" "CI run") for ${HEAD_SHA:0:12}, every one green"
    return 0
  fi
  hook="$(ci_push_gated)"
  if [ -z "$hook" ]; then
    [ -n "$runs" ] || fail "no CI run for HEAD (${HEAD_SHA:0:12}) and this checkout's pre-push hook \
is not goh's push gate, so nothing local says HEAD was gated -- re-run the release once CI has run \
(every step is idempotent), or --no-ci-check"
    ci_name_runs "$pending"
    fail "CI on HEAD (${HEAD_SHA:0:12}) is still pending (above) and this checkout's pre-push hook is \
not goh's push gate, so hosted CI is no advisory here -- nothing tagged; re-run the release once it \
finishes, or --no-ci-check"
  fi
  state="has no run yet"
  if [ -n "$pending" ]; then
    state="still pending (above)"
    while IFS= read -r line; do warn "$line"; done < <(awk -F'\t' \
      '{ printf "  %s: %s -- %s\n", $3, $1, $4 }' <<<"$pending")
  fi
  warn "CI on HEAD (${HEAD_SHA:0:12}) ${state}: advisory, tagged on the local push gate (${hook}); \
loopctl tick re-reads the run and names a red one"
}

release_ci_step() { # <ci_check: 1, or 0 for --no-ci-check>
  begin "ci" "the CI runs for HEAD"
  HEAD_SHA="$(git rev-parse HEAD)"
  if [ "$1" = 0 ]; then
    warn "skipped (--no-ci-check): tagging ${HEAD_SHA:0:12} over whatever its CI says"
    return 0
  fi
  if [ -z "$(git ls-files -- '.github/workflows/*.yml' '.github/workflows/*.yaml')" ]; then
    info "no CI: this repo tracks no .github/workflows, so there is no run to read (not a green)"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    # No network in a dry run: what origin has is read from the last fetch.
    if [ "$(git rev-parse -q --verify "refs/remotes/origin/${BRANCH}" || true)" = "$HEAD_SHA" ]; then
      plan "origin/${BRANCH} (last fetched) is HEAD: no push before CI"
    elif [ "$DO_PUSH" = 0 ]; then
      plan "refuse if origin/${BRANCH} is not HEAD: --no-push forbids pushing it for its CI"
    else
      plan "git push origin ${PUSH_REFSPEC} (the gated push) if origin lacks ${HEAD_SHA:0:12}, so CI runs on it"
    fi
    plan "gh run list --commit ${HEAD_SHA:0:12}, read once: refuse a red; tag a green, and a \
pending or missing run as advisory over goh's push gate (refused without one)"
    return 0
  fi
  [ -n "$GH_REPO_SLUG" ] || fail "this repo has CI workflows but origin (${ORIGIN_URL}) is not a \
GitHub remote, so its runs cannot be read -- --no-ci-check to tag without them"
  command -v gh >/dev/null 2>&1 || fail "gh is not installed, so the CI runs for HEAD cannot be \
read -- install it, or --no-ci-check to tag without them"
  # Actions DISABLED (R3.13, sys_updater): the workflows are tracked and no run will ever come, so
  # pushing early would be for nothing. The API says so; an unreadable answer (no admin rights,
  # offline) leaves the read below to decide, as before.
  local enabled
  enabled="$(gh api "repos/$GH_REPO_SLUG/actions/permissions" --jq .enabled 2>/dev/null \
      </dev/null || true)"
  if [ "$enabled" = false ]; then
    info "no CI: GitHub Actions is disabled for $GH_REPO_SLUG (its actions/permissions says \
enabled=false), so no run will come for ${HEAD_SHA:0:12} (not a green)"
    return 0
  fi
  ci_push_head
  ci_read
}
