# shellcheck shell=bash
# tools/release-kit/ci_step.sh -- step 0 of release.sh: the CI runs for the commit being tagged.
# Sourced by release.sh (from $GOH, the HEAD export), never run; it reads the kit's globals
# (DRY_RUN DO_PUSH BRANCH GH_REPO_SLUG ORIGIN_URL) and its helpers (begin plan fail).
#
# The order is push -> watch -> tag, in the kit, never by hand (GOH.RELCI). A wrapper commits
# "chore: version X" and calls the kit with that commit unpushed; GitHub had no run for it, so this
# step refused "no CI run for HEAD" and every such release needed a person to push, wait for CI and
# re-run (ztools v3.12.2, 2026-11-07). Now, when origin/<branch> is not HEAD, the branch is pushed
# through the NORMAL push (its pre-push gate runs; never --no-verify) and the runs for HEAD are
# watched under a deadline:
#
#   a red run      refuses at once, naming it -- waiting longer cannot turn it green;
#   pending / none polls every GOH_RELEASE_CI_POLL s (default 20) until GOH_RELEASE_CI_WAIT s
#                  (default 1800) have passed, then refuses naming what is still pending -- the
#                  kit is idempotent, so the fix is to re-run it once CI has finished;
#   all green      tags.
#
# --no-push with an unpushed HEAD refuses: no CI can run on a commit origin does not have.
# Tests: tests/test_release_kit_ci.py (the fake gh lists runs only for commits origin holds).

ci_wait_secs() { # <name> <value> -- the value, when it is a whole number of seconds
  case "$2" in
    '' | *[!0-9]*) die "step 'args': $1 must be a whole number of seconds (got '$2')" ;;
  esac
  printf '%s' "$2"
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

# ci_watch -- wait for every run for HEAD to finish green; refuse a red at once, and pending or
# missing runs at the deadline.
ci_watch() {
  local deadline=$((SECONDS + CI_WAIT)) said=0 runs red pending
  while :; do
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
    if [ "$SECONDS" -ge "$deadline" ]; then
      [ -n "$runs" ] || fail "no CI run for HEAD (${HEAD_SHA:0:12}) after ${CI_WAIT}s -- the watch \
timed out (GOH_RELEASE_CI_WAIT); a tag over no run is a tag over an unknown. Re-run the release \
once CI has run (every step is idempotent)"
      ci_name_runs "$pending"
      fail "CI on HEAD (${HEAD_SHA:0:12}) is still pending (above): the watch timed out after \
${CI_WAIT}s (GOH_RELEASE_CI_WAIT) -- nothing tagged; re-run the release once it finishes"
    fi
    [ "$said" = 1 ] || info "waiting up to ${CI_WAIT}s for CI on ${HEAD_SHA:0:12} \
(GOH_RELEASE_CI_WAIT; polling every ${CI_POLL}s)"
    said=1
    sleep "$CI_POLL"
  done
}

release_ci_step() { # <ci_check: 1, or 0 for --no-ci-check>
  begin "ci" "the CI runs for HEAD"
  HEAD_SHA="$(git rev-parse HEAD)"
  CI_WAIT="$(ci_wait_secs GOH_RELEASE_CI_WAIT "${GOH_RELEASE_CI_WAIT:-1800}")"
  CI_POLL="$(ci_wait_secs GOH_RELEASE_CI_POLL "${GOH_RELEASE_CI_POLL:-20}")"
  if [ "$1" = 0 ]; then
    warn "skipped (--no-ci-check): tagging ${HEAD_SHA:0:12} over whatever its CI says"
    return 0
  fi
  if [ -z "$(git ls-files -- '.github/workflows/*.yml' '.github/workflows/*.yaml')" ]; then
    info "no CI: this repo tracks no .github/workflows, so there is no run to wait for (not a green)"
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
    plan "gh run list --commit ${HEAD_SHA:0:12}: wait up to ${CI_WAIT}s (GOH_RELEASE_CI_WAIT) for \
every run to finish green; refuse a red at once, pending or missing at the deadline"
    return 0
  fi
  [ -n "$GH_REPO_SLUG" ] || fail "this repo has CI workflows but origin (${ORIGIN_URL}) is not a \
GitHub remote, so its runs cannot be read -- --no-ci-check to tag without them"
  command -v gh >/dev/null 2>&1 || fail "gh is not installed, so the CI runs for HEAD cannot be \
read -- install it, or --no-ci-check to tag without them"
  # Actions DISABLED (R3.13, sys_updater): the workflows are tracked and no run will ever come, so
  # waiting would only time out, and pushing early would be for nothing. The API says so; an
  # unreadable answer (no admin rights, offline) leaves the watch below to decide, as before.
  local enabled
  enabled="$(gh api "repos/$GH_REPO_SLUG/actions/permissions" --jq .enabled 2>/dev/null \
      </dev/null || true)"
  if [ "$enabled" = false ]; then
    info "no CI: GitHub Actions is disabled for $GH_REPO_SLUG (its actions/permissions says \
enabled=false), so no run will come for ${HEAD_SHA:0:12} (not a green)"
    return 0
  fi
  ci_push_head
  ci_watch
}
