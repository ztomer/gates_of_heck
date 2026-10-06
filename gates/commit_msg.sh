#!/usr/bin/env bash
# commit_msg.sh <message-file> -- the stock commit-msg hook's body: `goh commit-class` on the
# message, when the repo opts in with GOH_COMMIT_CLASS in its .gatesrc (roadmap Phase 8). A fix or
# perf commit names its Class and Siblings in its trailer block; a third instance of a class says
# how this one ends it. `git commit --no-verify` skips this, and push_gate.sh asks again over the
# pushed range, so the skip does not survive the push.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(git rev-parse --show-toplevel)"
on=""
if [ -f "$root/.gatesrc" ]; then
    # Read in a CHILD, by name: .gatesrc's other keys never reach the check (push_gate.sh's rule).
    on="$(bash -c 'set -a; . "$1"; printf %s "${GOH_COMMIT_CLASS-}"' _ "$root/.gatesrc")" \
        || { echo "✗ commit-msg: cannot read $root/.gatesrc" >&2; exit 1; }
fi
[ -n "$on" ] || exit 0
exec bash "$HERE/goh.sh" commit-class "$1"
exit
} # parse-guard
