#!/usr/bin/env bash
# quiet.sh [--max-load N] [--settle S] [--deadline D] [--label L] -- CMD... -- measure when quiet.
#
# Every wall-clock number in docs/BACKLOG.md needs a quiet box (load < 4), and for two days every
# one was taken at load 7-31 beside other sessions' gates (2026-10-08). A QUEUE, not a demand:
#
#   1. wait for a window -- the 1-minute load under --max-load (4) -- holding NOTHING, so no
#      session waits on a measurement that is only waiting itself;
#   2. then hold the host (lib/bench_lock.sh: no new goh gate starts, the running ones finish) and
#      the desktop (lib/desktop_lock/, which ZoneWM's probes respect), and check the load again for
#      up to --settle seconds (120): gates that drained still sit in the 1-minute average;
#   3. still busy -- work goh cannot hold off (an `xctest` started by a Makefile, Xcode,
#      Spotlight): the window closed, so let go and go back to 1. Nobody else is asked to do
#      anything; a measurement runs when the box goes quiet on its own (a night, a lull);
#   4. past --deadline seconds (14400, 4 h) with no window: REFUSE, naming the busiest processes.
#
# Then CMD runs, with the load printed at its start and end; this exits with CMD's status. Two
# measurements queue on the host lock. The desktop lock's own max hold (900 s, a peer's rule)
# bounds a measurement that also needs it. Test seams: GOH_BENCH_LOADAVG (a command printing the
# load), GOH_BENCH_POLL (seconds between reads, 15), GOH_BENCH_DESKTOP_LOCK_DIR.
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

max_load=4 settle=120 deadline=14400 label="a measurement" poll="${GOH_BENCH_POLL:-15}"
while [ $# -gt 0 ]; do
    case "$1" in
        --max-load) max_load="${2:?--max-load needs a value}"; shift 2 ;;
        --settle) settle="${2:?--settle needs a value}"; shift 2 ;;
        --deadline) deadline="${2:?--deadline needs a value}"; shift 2 ;;
        --label) label="${2:?--label needs a value}"; shift 2 ;;
        --) shift; break ;;
        *) die "quiet.sh: unknown argument $1 (usage: quiet.sh [--max-load N] [--settle S] [--deadline D] -- CMD...)" ;;
    esac
done
[ $# -gt 0 ] || die "quiet.sh: no command to run (usage: quiet.sh [--max-load N] [--settle S] -- CMD...)"

load1() { # the 1-minute load average
    if [ -n "${GOH_BENCH_LOADAVG:-}" ]; then
        "$GOH_BENCH_LOADAVG"
    elif [ -r /proc/loadavg ]; then
        read -r l _ </proc/loadavg
        printf '%s' "$l"
    else
        l="$(sysctl -n vm.loadavg)"
        l="${l#\{ }"
        printf '%s' "${l%% *}"
    fi
}
below() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a < b) }'; }

trap 'desktop_lock_release; bench_lock_release' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
label="$label (tools/quiet.sh)"
elapsed() { awk -v a="$1" -v b="$(date +%s)" 'BEGIN { print b - a }'; }
over() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a >= b) }'; }
t0="$(date +%s)" said=""
while :; do
    until below "$(load1)" "$max_load"; do                  # 1. a window, holding nothing
        if over "$(elapsed "$t0")" "$deadline"; then
            err "the host is not quiet: no window under load $max_load in ${deadline}s (load $(load1)); busiest:"
            ps -Ao pcpu,comm -r | head -6 >&2 || true # head closes early: ps's SIGPIPE is not the verdict
            exit 1
        fi
        [ -n "$said" ] || info "quiet: load $(load1), waiting for a window under $max_load (holding nothing)"
        said=1
        sleep "$poll"
    done
    bench_lock_exclusive "$label" || die "quiet.sh: could not hold the host (above)"  # 2. hold
    desktop_lock_acquire "$label"
    t1="$(date +%s)"
    until below "$(load1)" "$max_load"; do
        over "$(elapsed "$t1")" "$settle" && break
        sleep "$(awk -v p="$poll" 'BEGIN { print (p < 5 ? p : 5) }')"
    done
    below "$(load1)" "$max_load" && break
    warn "quiet: the window closed (load $(load1) after ${settle}s held): letting go, waiting again"  # 3.
    desktop_lock_release
    bench_lock_release
    said=""
done
info "quiet: load $(load1) (max $max_load), every gate held -- running: $*"
rc=0
"$@" || rc=$?
info "quiet: load at the end $(load1); exit $rc"
exit "$rc"
exit
} # parse-guard
