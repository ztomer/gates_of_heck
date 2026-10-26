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
# The gate proves WITH the proven-step cache on (G2f, GOH_PROFILE_PROVEN): gate_profile.sh keeps it
# off by default because a profile of cache hits measures the cache, but landing wants the verdict,
# and the push that follows the fast-forward is the same tree -- it reuses what this gate recorded
# instead of re-running 168 s of it (tests/test_proven.py, both directions). A caller that set
# GOH_PROFILE_PROVEN keeps its value.
#
# What it claims it verifies (G15): the tip must differ from main's, the tree must be clean, main must
# equal the gated tip afterwards, and GOH_LAND_EXPECT="path ..." names paths the landed range must change;
# anything else is a non-zero exit naming what did not happen.
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
# Refuse BEFORE the gate what could never land (G15): a pre-commit hook that refused the commit left
# the tip at the base, and "landed" was printed over a no-op merge. Landing is a state change, so the
# state is checked, not the exit code of the merge.
base="$(git -C "$main" rev-parse HEAD)"
if [ "$tip" = "$base" ]; then
    err "land.sh: nothing to land -- $branch @ ${tip:0:8} IS the main tip (did a hook refuse the commit?) -- nothing landed"
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    err "land.sh: uncommitted changes to tracked files in $here -- the gate would judge a tree that is not ${tip:0:8} -- nothing landed"
    exit 1
fi
info "gate: $gate"
# In the gate's environment only, never exported: an exported GOH_ key is gate bookkeeping and must be
# verdict-free (tests/test_verdict_free_keys.py), and this one is one process's setting.
if ! GOH_PROFILE_PROVEN="${GOH_PROFILE_PROVEN-1}" bash -c "$gate"; then
    err "land.sh: the gate failed on ${tip:0:8} -- nothing landed"
    exit 1
fi
now="$(git rev-parse HEAD)"
if [ "$now" != "$tip" ]; then
    err "land.sh: $branch moved while it was gated (${tip:0:8} -> ${now:0:8}) -- nothing landed; land again"
    exit 1
fi
base="$(git -C "$main" rev-parse HEAD)"   # re-read: main may have moved during a minutes-long gate
if ! git -C "$main" merge-base --is-ancestor "$base" "$tip"; then
    err "land.sh: main moved on since $branch branched -- rebase onto it, then land again"
    exit 1
fi
# What the range changes is known before main moves, so a missing change lands nothing.
changed="$(git -C "$main" diff --name-only "$base" "$tip")"
for want in ${GOH_LAND_EXPECT:-}; do
    case $'\n'"$changed"$'\n' in
        *$'\n'"$want"$'\n'*) ;;
        *)  err "land.sh: the landed range ${base:0:8}..${tip:0:8} does not change $want (GOH_LAND_EXPECT) -- the intended change is not in it"
            exit 1 ;;
    esac
done
git -C "$main" merge --ff-only -q "$tip"
# Verify the claim before making it: main must now BE the gated tip and differ from where it was.
after="$(git -C "$main" rev-parse HEAD)"
if [ "$after" != "$tip" ] || [ "$after" = "$base" ]; then   # (also: nothing to land if the tip IS main)
    err "land.sh: main is at ${after:0:8} after the merge, not the gated ${tip:0:8} (was ${base:0:8}) -- nothing landed"
    exit 1
fi
# The record a push reads (GOH_LANDED_ONLY): this SHA was gated here and merged. In the common git
# dir, so it outlives the worktree that landed it, and by SHA, so a branch that moved is not it.
printf '%s\n' "$tip" >>"$(git -C "$main" rev-parse --path-format=absolute --git-common-dir)/goh-landed"
ok "landed ${tip:0:8} on $(git -C "$main" rev-parse --abbrev-ref HEAD) in $main (nothing pushed)"
exit
} # parse-guard
