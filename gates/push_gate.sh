#!/usr/bin/env bash
# push_gate.sh — run the full gate on the COMMIT being pushed, not on the working tree.
#
# THE HOLE. `pre-push` used to `exec tools/gate.sh --full` in the checkout, so the gate built and
# tested whatever was on disk at that moment. Measured 2026-09-21 on ZoneWM: a push's cold gate
# was in its test phase (`swift test` recompiles changed sources) while the next round's edits
# were landing in Sources/ — so the gate certified a tree that was neither the pushed commit nor
# any commit, and would have pushed a docs change on the strength of tests run over unrelated,
# unfinished code. The other direction is worse and quieter: uncommitted fixes on disk make a
# broken commit pass. A pre-push gate that reads the working tree is a gate on the wrong object.
#
# THE FIX. For each ref being pushed, check the pushed sha out into a throwaway `git worktree`
# and run the repo's own `tools/gate.sh --full` THERE. The worktree has the tracked files and
# nothing else, which is exactly what the remote will have -- with one documented exception:
# `GOH_EXPORT_KEEP` in `.gatesrc` names ignored files the build genuinely reads (a compile-flag
# marker, a local toolchain pin); they are copied in so the gate tests the tree AS CONFIGURED.
# Cold-build cost is unchanged (the swift gate wiped .build anyway); the checkout is ~1 s.
#
# The worktree lives under ~/.cache/goh/push (GOH_PUSH_WORKTREES overrides), never inside the
# repo (a nested worktree is a tracked-file scan's worst day) and never under the temp directory
# (see the SwiftLint note below). It is removed on every exit path; a worktree left behind by a
# SIGKILL is pruned by the next run (`git worktree prune`), so nothing accumulates.
#
# Protocol: git feeds `<local ref> <local sha> <remote ref> <remote sha>` lines on stdin, and passes
# the remote's name as $1. A delete (local sha all zeros) has nothing to test. Several refs are
# gated one after another; a failure stops the push.
#
# WHAT IS NOT GATED, and why that is not a pass over nothing. The gate certifies code the remote is
# about to GAIN. Two kinds of ref gain it none, and each is skipped with a line saying so:
#   - a ref whose commit the remote already has (reachable from one of its tracking refs). ZoneWM,
#     2026-09-22: `git push --follow-tags` carried six release tags that had never been pushed, all
#     on commits the remote had held for days. The gate rebuilt the oldest with today's toolchain,
#     failed on a switch a later SDK made non-exhaustive, and refused a push whose only new code was
#     elsewhere. Six tags would also have cost six cold gates.
#   - a ref whose commit was already gated earlier in this same push. A release pushes `main` and
#     `vX.Y.Z` on one commit; one gate certifies both.
#
# A RED RUN KEEPS ITS EVIDENCE. ZoneWM, 2026-09-22: a push was refused, the caller had kept only the
# last lines of the output, and the re-run of the same commit passed. The failure was a flake, and
# nobody could say which test, because the only copy of the gate's output was a scrollback that no
# longer existed. Every run is now teed to a log under ~/.cache/goh/push-logs (GOH_PUSH_LOGS
# overrides). A green run deletes its log; a red one keeps it, prints its path, and the newest
# `keep_failed_logs` are kept. The cost: the gate writes to a pipe, so its colours are off.
set -euo pipefail

GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"
. "$GOH/tui/lib.sh"

root="$(git rev-parse --show-toplevel)"
gate="$root/tools/gate.sh"
[ -f "$gate" ] || die "pre-push: $root/tools/gate.sh missing — run '$GOH/install.sh $root'"

# Ignored files the export must carry, from the repo's .gatesrc (space-separated, relative).
GOH_EXPORT_KEEP=""
[ -f "$root/.gatesrc" ] && . "$root/.gatesrc"

zero="0000000000000000000000000000000000000000"
remote_name="${1:-}"
gated_commits=" "
log_root="${GOH_PUSH_LOGS:-$HOME/.cache/goh/push-logs}"
keep_failed_logs=20
worktree=""
cleanup() {
    if [ -n "$worktree" ] && [ -d "$worktree" ]; then
        git -C "$root" worktree remove --force "$worktree" >/dev/null 2>&1 || rm -rf "$worktree"
    fi
}
trap cleanup EXIT INT TERM

git -C "$root" worktree prune >/dev/null 2>&1 || true

gated=0
while read -r local_ref local_sha _remote_ref _remote_sha; do
    [ -n "${local_sha:-}" ] || continue
    [ "$local_sha" = "$zero" ] && continue          # a delete: nothing to test
    # A tag's sha is the tag OBJECT; the code under test is the commit it points at.
    commit="$(git -C "$root" rev-parse --verify --quiet "${local_sha}^{commit}" || echo "$local_sha")"
    short="$(git -C "$root" rev-parse --short "$commit")"
    case "$gated_commits" in
        *" $commit "*) info "pre-push: $local_ref @ $short — gated earlier in this push"; continue ;;
    esac
    if [ -n "$remote_name" ] && [ -n "$(git -C "$root" for-each-ref --contains "$commit" \
            --format='%(refname)' "refs/remotes/$remote_name/")" ]; then
        info "pre-push: $local_ref @ $short — $remote_name already has this commit; nothing new to gate"
        continue
    fi
    # UNDER $HOME, physical path, never under the temp directory. SwiftLint 0.65.1's baseline
    # (repo-relative paths, verified path-independent by ZoneWM's relativize_lint_baseline.py)
    # matches NOTHING when the tree sits under /private/tmp or /private/var/folders -- every
    # recorded violation fires as new -- and matches everything under /Users/... (measured
    # 2026-09-21 with worktrees of one commit at ~/wt, /Users/Shared/wt, <repo>/.build/wt: 0
    # violations; /private/tmp/wt: 12 -- even against a baseline swiftlint had just written
    # THERE). The cause is inside SwiftLint's path relativisation; the fix that holds without
    # theory is to gate where the tools were calibrated: a directory beside the user's checkouts.
    # `pwd -P` besides, so a symlinked component can never be the difference.
    export_root="${GOH_PUSH_WORKTREES:-$HOME/.cache/goh/push}"
    mkdir -p "$export_root"
    worktree="$(mktemp -d "$export_root/XXXXXX")"
    worktree="$(cd "$worktree" && pwd -P)"
    rmdir "$worktree"                                # git wants to create it
    section "pre-push: gating $local_ref @ $short in a clean worktree"
    git -C "$root" worktree add --detach --quiet "$worktree" "$commit"
    for f in $GOH_EXPORT_KEEP; do
        if [ -e "$root/$f" ]; then
            mkdir -p "$worktree/$(dirname "$f")"
            cp -R "$root/$f" "$worktree/$f"
            info "carried ignored file into the export: $f"
        fi
    done
    mkdir -p "$log_root"
    log="$log_root/$(basename "$root")-$short-$(date +%Y%m%dT%H%M%S).log"
    set +e
    (cd "$worktree" && bash "$worktree/tools/gate.sh" --full) 2>&1 | tee "$log"
    status="${PIPESTATUS[0]}"
    set -e
    if [ "$status" -ne 0 ]; then
        err "pre-push: the gate failed on $short — nothing pushed"
        err "pre-push: the full gate output is kept at $log"
        # Bounded: the newest failures are evidence, the rest is clutter.
        find "$log_root" -name '*.log' -type f -print0 | xargs -0 ls -t | tail -n "+$((keep_failed_logs + 1))" \
            | while IFS= read -r old; do rm -f "$old"; done
        exit 1
    fi
    rm -f "$log"
    cleanup; worktree=""
    gated=$((gated + 1))
    gated_commits="$gated_commits$commit "
done

[ "$gated" -gt 0 ] || info "pre-push: nothing to gate (deletes, or commits the remote already has)"
