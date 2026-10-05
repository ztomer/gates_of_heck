#!/usr/bin/env bash
# gate_profile.sh -- where does a repo's pre-push time go? The P0 instrument's front end.
#
#   tools/gate_profile.sh <repo> [top]    # profile <repo>'s push gate on HEAD; nothing is pushed
#   GOH_PROFILE_PROVEN=1 tools/gate_profile.sh <repo>   # keep the proven-step cache (default: off)
#
# Feeds gates/push_gate.sh the ref line git would, for a remote that has nothing (so no ref is
# skipped as already-held), with GOH_TIMINGS set: every step of every nested gate appends one JSON
# line (lib/step_timings.py). Then prints the slowest steps and each label summed across every
# place it ran -- the view that shows a 7 s step repeated in 29 crates.
#
# The proven cache is OFF by default because the question is what the work COSTS; a profile of
# cache hits measures the cache. Exit: the push gate's own code (the report prints either way).
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOH="$(cd "$HERE/.." && pwd)"
[ $# -ge 1 ] && [ -d "$1" ] || { echo "usage: gate_profile.sh <repo> [top]" >&2; exit 2; }
repo="$(cd "$1" && pwd -P)"
top="${2:-20}"
sha="$(git -C "$repo" rev-parse HEAD)" || { echo "gate_profile: $repo is not a git repo" >&2; exit 2; }
out="${GOH_TIMINGS:-$(mktemp "${TMPDIR:-/tmp}/goh-timings.XXXXXX")}"
zero=0000000000000000000000000000000000000000
proven=0
[ -n "${GOH_PROFILE_PROVEN:-}" ] && proven=1
t0=$(date +%s)
printf 'refs/heads/goh-profile %s refs/heads/goh-profile %s\n' "$sha" "$zero" \
    | (cd "$repo" && GOH_TIMINGS="$out" GOH_PROVEN="$proven" \
        bash "$GOH/gates/push_gate.sh" goh-profile "$repo") >"$out.log" 2>&1
rc=$?
printf '%s @ %s: push gate exit %s in %ss (log: %s)\n' \
    "$(basename "$repo")" "${sha:0:12}" "$rc" "$(( $(date +%s) - t0 ))" "$out.log"
python3 "$GOH/lib/step_timings.py" report "$out" "$top"
printf 'timings: %s\n' "$out"
exit "$rc"
} # parse-guard
