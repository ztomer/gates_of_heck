#!/usr/bin/env bash
# commit_gate.sh -- what the stock pre-commit runs: the repo's gate in STAGED scope, and the house
# structural layer even when that gate leaves it out (BACKLOG 6.7).
#
# WHY A FILE HERE AND NOT IN THE HOOK. install.sh COPIES the hooks, so every change to the hook's
# logic reached no repo until it re-installed: 14 repos carried a retired stock hook on 2026-10-12.
# The pre-push hook already only delegates (gates/push_gate.sh); the pre-commit now does the same,
# so what a commit runs is decided here, at runtime, for every repo at once.
#
# THE HOLE. The hook used to `exec tools/gate.sh --staged` and trust it to run structural.sh.
# old_phones' gate never did, so emoji and the line cap were enforced nowhere there, and its
# 523-line preflight.sh went in unrefused. Now structural.sh leaves a mark (scope and tree) in
# GOH_STRUCTURAL_MARK when it runs, and a gate that passed without marking `--staged` for THIS
# tree gets structural run after it, with the gap named. The tree is part of the mark because a
# consumer's test suite may run structural.sh inside a fixture, which is not this repo's commit.
#
# Without a tools/gate.sh, structural runs alone, as it always did.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
# git binds this hook to its repository (GIT_DIR, GIT_INDEX_FILE -- absolute from a linked
# worktree), and a test suite the gate runs that builds a scratch repo would act on THIS one
# (media_server, 2026-10-08: core.bare=true, a stray commit, three stashes). Dropped before anything
# is spawned; the index being committed travels as GOH_HOOK_INDEX_FILE (contract #12).
goh_hook_unbind || exit $?

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../tui/lib.sh"
root="$(git rev-parse --show-toplevel)"
gate="$root/tools/gate.sh"
structural="$HERE/structural.sh"

if [ ! -f "$gate" ]; then
    exec bash "$structural" --staged
fi

mark="$(mktemp "${TMPDIR:-/tmp}/goh-structural-mark.XXXXXX")"
trap 'rm -f "$mark"' EXIT
GOH_STRUCTURAL_MARK="$mark" bash "$gate" --staged
# `grep -F -x` on the whole line: the scope and the tree it ran in, as structural.sh wrote them.
if ! grep -qFx -- "--staged	$root" "$mark"; then
    warn "pre-commit: tools/gate.sh --staged passed without running the house structural layer"
    warn "  (emoji, conflict markers, the line cap, secrets...) -- running it now; call"
    warn "  \"\$GOH/gates/structural.sh\" --staged from tools/gate.sh to make this line go away"
    bash "$structural" --staged
fi
exit
} # parse-guard
