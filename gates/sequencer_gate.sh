#!/usr/bin/env bash
# sequencer_gate.sh <hook> [args] -- the body of the stock post-rewrite and pre-applypatch hooks:
# the conflict-marker check for the commits git makes WITHOUT running pre-commit.
#
# Measured on git 2.56 (tests/test_sequencer_markers.py): a `git rebase` pick, conflicted and
# resolved or clean, runs no pre-commit and no commit-msg, so a resolution that kept a marker was
# committed unchecked (a CHANGELOG.md diff3 base section, 2026-10-08, caught only at push).
# `git am` and `rebase --apply` run pre-applypatch per patch. Cherry-pick, revert and merge
# `--continue` already run pre-commit.
#
#   post-rewrite rebase   stdin `<old> <new>` lines: what each NEW commit changed. git ignores the
#                         exit, and the rebase is done, so this reports -- loudly, naming each
#                         commit -- and the push gate refuses what it names.
#   post-rewrite amend    nothing: the amend ran pre-commit, or skipped it with --no-verify.
#   pre-applypatch        the index the patch made; non-zero REFUSES it (`git am --no-verify` skips).
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tui/lib.sh
. "$HERE/../tui/lib.sh"

hook="${1:-}"
case "$hook" in
    post-rewrite)
        [ "${2:-}" = "rebase" ] || exit 0
        # A squash maps several old commits to one new: each new commit once, in order.
        new=()
        while read -r _old sha _extra; do
            [ -n "${sha:-}" ] && new+=("$sha")
        done < <(awk '!seen[$2]++')
        [ "${#new[@]}" -gt 0 ] || exit 0
        rc=0
        bash "$HERE/goh.sh" markers --commits "${new[@]}" || rc=$?
        if [ "$rc" -eq 1 ]; then
            err "post-rewrite: the rebase COMMITTED the conflict markers above"
            err "  it has finished, so nothing was refused: fix each commit named there before"
            err "  pushing (git rebase -i <commit>~1, mark it edit) -- the pre-push gate refuses them"
        fi
        exit "$rc" ;;
    pre-applypatch)
        rc=0
        bash "$HERE/goh.sh" markers --staged || rc=$?
        if [ "$rc" -eq 1 ]; then
            err "pre-applypatch: the patch was NOT committed -- remove the markers above, git add,"
            err "  then git am --continue (or git rebase --continue under --apply)"
        fi
        exit "$rc" ;;
    *)
        err "sequencer_gate.sh: unknown hook '$hook' (post-rewrite | pre-applypatch)"
        exit 2 ;;
esac
exit
} # parse-guard
