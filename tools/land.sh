#!/usr/bin/env bash
# land.sh -- from a worktree: gate the branch tip, then fast-forward the main checkout to THAT sha.
#
# The house workflow lands a branch only after its own push gate is green on the tip
# (`tools/gate_profile.sh .`). Done by hand it was `gate; merge`, and on 2026-10-08 a `;` where
# `&&` belonged moved main onto a red gate run (engineering rule #3: gates are structural, not
# disciplinary). So the two are one command:
#
#   1. the tip is read once, and the gate (GOH_LAND_GATE, default `tools/gate_profile.sh .`) runs;
#   2. a red gate lands nothing; a tip that moved while it was gated lands nothing (the gate
#      judged a different commit);
#   3. the main checkout (the first `git worktree list` entry) merges the GATED SHA with
#      `--ff-only` -- never the branch name, which may have moved -- and a main that moved on is
#      named: rebase onto it and land again.
#
# Nothing is pushed. The gated SHA is recorded in the common git dir (`goh-landed`), which a repo
# that sets GOH_LANDED_ONLY=<branch> has its push read: a tip that did not come through here is
# refused (BACKLOG 1.5). Pinned by tests/test_land.py.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"

here="$(git rev-parse --show-toplevel)"
main="$(git worktree list --porcelain | { read -r _ path; printf '%s' "$path"; })"
if [ "$(cd "$here" && pwd -P)" = "$(cd "$main" && pwd -P)" ]; then
    err "land.sh: run it from the branch's worktree, not the main checkout ($main)"
    exit 2
fi
tip="$(git rev-parse HEAD)"
branch="$(git rev-parse --abbrev-ref HEAD)"
gate="${GOH_LAND_GATE:-tools/gate_profile.sh .}"

section "land $branch @ ${tip:0:8} -> $main"
info "gate: $gate"
if ! bash -c "$gate"; then
    err "land.sh: the gate failed on ${tip:0:8} -- nothing landed"
    exit 1
fi
now="$(git rev-parse HEAD)"
if [ "$now" != "$tip" ]; then
    err "land.sh: $branch moved while it was gated (${tip:0:8} -> ${now:0:8}) -- nothing landed; land again"
    exit 1
fi
if ! git -C "$main" merge-base --is-ancestor "$(git -C "$main" rev-parse HEAD)" "$tip"; then
    err "land.sh: main moved on since $branch branched -- rebase onto it, then land again"
    exit 1
fi
git -C "$main" merge --ff-only -q "$tip"
# The record a push reads (GOH_LANDED_ONLY): this SHA was gated here and merged. In the common git
# dir, so it outlives the worktree that landed it, and by SHA, so a branch that moved is not it.
printf '%s\n' "$tip" >>"$(git -C "$main" rev-parse --path-format=absolute --git-common-dir)/goh-landed"
ok "landed ${tip:0:8} on $(git -C "$main" rev-parse --abbrev-ref HEAD) in $main (nothing pushed)"
exit
} # parse-guard
