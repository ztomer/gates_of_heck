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
# The gate is bracketed by a control of each kind its steps declare (lib/step_control.py), one
# before and one after, so the report can divide each step's CPU by fixed work run under the same
# load (BACKLOG 5.3): shell always, Rust when the repo builds Rust. A control that fails is noted
# and skipped -- the instrument is never the reason a profile goes red.
#
# The proven cache is OFF by default because the question is what the work COSTS; a profile of
# cache hits measures the cache. Exit: the push gate's own code (the report prints either way).
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
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
kinds=shell
[ -f "$repo/Cargo.toml" ] && kinds="shell rust"
controls() {
    # shellcheck disable=SC2086 # one word per kind
    GOH_TIMINGS="$out" python3 "$GOH/lib/step_control.py" run $kinds >>"$out.controls.log" 2>&1 \
        || printf 'gate_profile: a control did not run (log: %s.controls.log)\n' "$out" >&2
}
controls
t0=$(date +%s)
# The local ref is the SHA itself (the pinned form): push_gate.sh refuses a named ref that no longer
# names the gated commit, and a made-up branch name names nothing at all.
printf '%s %s refs/heads/goh-profile %s\n' "$sha" "$sha" "$zero" \
    | (cd "$repo" && GOH_TIMINGS="$out" GOH_PROVEN="$proven" \
        bash "$GOH/gates/push_gate.sh" goh-profile "$repo") >"$out.log" 2>&1
rc=$?
controls
printf '%s @ %s: push gate exit %s in %ss (log: %s)\n' \
    "$(basename "$repo")" "${sha:0:12}" "$rc" "$(( $(date +%s) - t0 ))" "$out.log"
python3 "$GOH/lib/step_timings.py" report "$out" "$top"
printf 'timings: %s\n' "$out"
# A red gate names its reason, not only its log: land.sh runs this as its gate, and a refusal that
# printed timings and a path left the failing tests three files away (2026-10-11).
[ "$rc" -eq 0 ] || python3 "$GOH/lib/fail_lines.py" "$out.log" >&2 || true
(exit "$rc")
exit
} # parse-guard
