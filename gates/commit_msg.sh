#!/usr/bin/env bash
# commit_msg.sh <message-file> -- the stock commit-msg hook's body: `goh commit-class` on the
# message, when the repo opts in with GOH_COMMIT_CLASS in its .gatesrc (roadmap Phase 8), and
# `goh red-run` when it opts in with GOH_RED_RUN (BACKLOG 4.11). A fix or perf commit names its
# Class and Siblings in its trailer block; a third instance of a class says how this one ends it; a
# commit that adds a test names the mutation that turned it red. `git commit --no-verify` skips
# this, and push_gate.sh asks again over the pushed range, so the skip does not survive the push.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(git rev-parse --show-toplevel)"
class_on="" red_on=""
if [ -f "$root/.gatesrc" ]; then
    # Read in a CHILD, by name: .gatesrc's other keys never reach the check (push_gate.sh's rule).
    keys="$(bash -c 'set -a; . "$1"; printf "%s\n%s" "${GOH_COMMIT_CLASS-}" "${GOH_RED_RUN-}"' \
        _ "$root/.gatesrc")" || { echo "✗ commit-msg: cannot read $root/.gatesrc" >&2; exit 1; }
    class_on="$(printf '%s\n' "$keys" | sed -n 1p)"
    red_on="$(printf '%s\n' "$keys" | sed -n 2p)"
fi
rc=0
[ -z "$class_on" ] || bash "$HERE/goh.sh" commit-class "$1" || rc=1
[ -z "$red_on" ] || bash "$HERE/goh.sh" red-run "$1" || rc=1
[ "$rc" -eq 0 ]   # under set -e a refusal exits 1 here; the bare exit below is the parse guard's
exit
} # parse-guard
