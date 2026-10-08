#!/usr/bin/env bash
# quiet.sh [--max-load N] [--settle S] [--label L] -- CMD... -- run a measurement on a quiet host.
#
# Every wall-clock number in docs/BACKLOG.md needs a quiet box (load < 4), and for two days every
# one was taken at load 7-31 beside other sessions' gates (2026-10-08). This makes the box quiet
# instead of waiting for it to be:
#
#   1. the host is held (lib/bench_lock.sh): no new goh gate starts anywhere on it, and this waits
#      for the running ones to finish -- every other session's commit and push waits meanwhile;
#   2. the desktop is held (lib/desktop_lock/), which ZoneWM's desktop-driving probes respect;
#   3. the 1-minute load average must fall below --max-load (4) within --settle seconds (600),
#      because work goh cannot hold off (Xcode, Spotlight, a hand-run build) is still load. A box
#      that will not settle is REFUSED, naming its busiest processes -- a number taken over it
#      would be the noise this exists to remove.
#
# Then CMD runs, with the load printed at its start and end; this exits with CMD's status. The
# desktop lock's own max hold (900 s, a peer's rule) bounds a measurement that also needs it.
# GOH_BENCH_DESKTOP_LOCK_DIR points the desktop lock elsewhere (tests only).
#
#   tools/quiet.sh -- python3 tools/session_bench.py --repo . --cmd 'gates/structural.sh --full'
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=lib/bench_lock.sh
. "$GOH/lib/bench_lock.sh"
# shellcheck source=lib/desktop_lock/desktop_lock.sh
. "$GOH/lib/desktop_lock/desktop_lock.sh"
DESKTOP_LOCK_DIR="${GOH_BENCH_DESKTOP_LOCK_DIR:-$DESKTOP_LOCK_DIR}"

max_load=4 settle=600 label="a measurement"
while [ $# -gt 0 ]; do
    case "$1" in
        --max-load) max_load="${2:?--max-load needs a value}"; shift 2 ;;
        --settle) settle="${2:?--settle needs a value}"; shift 2 ;;
        --label) label="${2:?--label needs a value}"; shift 2 ;;
        --) shift; break ;;
        *) die "quiet.sh: unknown argument $1 (usage: quiet.sh [--max-load N] [--settle S] -- CMD...)" ;;
    esac
done
[ $# -gt 0 ] || die "quiet.sh: no command to run (usage: quiet.sh [--max-load N] [--settle S] -- CMD...)"

load1() { # the 1-minute load average
    if [ -r /proc/loadavg ]; then
        read -r l _ </proc/loadavg
    else
        l="$(sysctl -n vm.loadavg)"
        l="${l#\{ }"
        l="${l%% *}"
    fi
    printf '%s' "$l"
}
below() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'; }

trap 'desktop_lock_release; bench_lock_release' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
label="$label (tools/quiet.sh)"
bench_lock_exclusive "$label" || die "quiet.sh: could not hold the host (above)"
desktop_lock_acquire "$label"
deadline=$(($(date +%s) + settle))
until below "$(load1)" "$max_load"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        err "the host is not quiet: load $(load1) after ${settle}s with every gate held (max $max_load); busiest:"
        ps -Ao pcpu,comm -r | head -6 >&2 || true # head closes early: ps's SIGPIPE is not the verdict
        exit 1
    fi
    sleep 5
done
info "quiet: load $(load1) (max $max_load), every gate held -- running: $*"
rc=0
"$@" || rc=$?
info "quiet: load at the end $(load1); exit $rc"
exit "$rc"
exit
} # parse-guard
