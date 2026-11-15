#!/usr/bin/env bash
# loaded_suite.sh [-n N] [-- CMD...] -- goh's suite, once, under tools/load.py's N spinners.
#
# GOH.CLASS.HARNESS (2026-11-15): about nineteen flake items in a month were a test right on a quiet
# box and red at load 110-180 -- found by whichever land happened to run on a busy box, and billed
# to a change that had nothing to do with it. The release runs the suite ONCE under deliberate load
# (tools/release_goh.sh, before its land), so a load-sensitive test is red at the release that
# carries it, named, rather than at a random land later. tests/wall_clock_lint.py is the static half.
#
#   N     spinners, default the core count (load.py refuses more unless --oversubscribe)
#   CMD   default tools/pytest.sh
#
# The load is made the one way (tools/load.py): it waits for the gates in flight (at most 30 min,
# then exit 75, named), reaps its spinners pass or fail, and bounds the run at 60 min. The last line
# names the verdict, the spinners and the load average the run began on, for the release's log.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/../lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"

spinners="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
if [ "${1:-}" = "-n" ]; then
    spinners="${2:-}"
    shift 2 || true
fi
if ! [[ "$spinners" =~ ^[0-9]+$ ]]; then
    err "loaded_suite.sh: -n takes a spinner count (got '$spinners')"
    exit 2
fi
[ "${1:-}" = "--" ] && shift
cmd=("$@")
[ ${#cmd[@]} -gt 0 ] || cmd=("$GOH/tools/pytest.sh")

before="$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}' || true)"
[ -n "$before" ] || before="$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo unknown)"
section "the suite once under load: $spinners spinners on load $before"
rc=0
"$GOH_PY" "$GOH/tools/load.py" --wait --wait-max 30m --max 60m "$spinners" -- "${cmd[@]}" || rc=$?
if [ "$rc" -ne 0 ]; then
    err "loaded suite: exit $rc under $spinners spinners (load $before before they started): a test the box's load turns red, or the load tool's refusal above"
    exit "$rc"
fi
ok "loaded suite: passed under $spinners spinners (load $before before they started)"
exit
} # parse-guard
