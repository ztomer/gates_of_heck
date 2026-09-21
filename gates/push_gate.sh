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
# The worktree lives under $TMPDIR, never inside the repo (a nested worktree is a tracked-file
# scan's worst day). It is removed on every exit path; a worktree left behind by a SIGKILL is
# pruned by the next run (`git worktree prune`), so nothing accumulates.
#
# Protocol: git feeds `<local ref> <local sha> <remote ref> <remote sha>` lines on stdin. A
# delete (local sha all zeros) has nothing to test. Several refs are gated one after another;
# a failure stops the push.
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
    short="$(git -C "$root" rev-parse --short "$local_sha")"
    # PHYSICAL path, never the logical one. macOS's $TMPDIR is /var/folders/..., a symlink into
    # /private/var; tools that report real paths (swiftlint does) then disagree with a cwd spelled
    # through the symlink, and a path-keyed baseline matches nothing — every recorded violation
    # fired as new on the first worktree-gated push (ZoneWM, 2026-09-21, 169 of them).
    worktree="$(mktemp -d "${TMPDIR:-/tmp}/goh-push-XXXXXX")"
    worktree="$(cd "$worktree" && pwd -P)"
    rmdir "$worktree"                                # git wants to create it
    section "pre-push: gating $local_ref @ $short in a clean worktree"
    git -C "$root" worktree add --detach --quiet "$worktree" "$local_sha"
    for f in $GOH_EXPORT_KEEP; do
        if [ -e "$root/$f" ]; then
            mkdir -p "$worktree/$(dirname "$f")"
            cp -R "$root/$f" "$worktree/$f"
            info "carried ignored file into the export: $f"
        fi
    done
    if ! (cd "$worktree" && bash "$worktree/tools/gate.sh" --full); then
        err "pre-push: the gate failed on $short — nothing pushed"
        exit 1
    fi
    cleanup; worktree=""
    gated=$((gated + 1))
done

[ "$gated" -gt 0 ] || info "pre-push: nothing to gate (deletes only)"
