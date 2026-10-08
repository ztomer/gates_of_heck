#!/usr/bin/env bash
# quiet.sh [--max-load N] [--settle S] [--retry R] [--hold H] [--deadline D] [--label L] -- CMD...
# -- measure when quiet.
#
# Every wall-clock number in docs/BACKLOG.md needs a quiet box (load < 4), and for two days every
# one was taken at load 7-31 beside other sessions' gates (2026-10-08). A QUEUE, not a demand --
# and a queue that takes its PLACE first:
#
#   1. hold the host (lib/bench_lock.sh: no new goh gate starts, the running ones finish) and the
#      desktop (lib/desktop_lock/, which ZoneWM's probes respect). The first version waited for
#      the load to fall HOLDING NOTHING, and on a box a dozen sessions share it waited 2 h without
#      one window: every new gate started ahead of it (BACKLOG 3.1, a reader-preferring queue
#      starving its writer);
#   2. with every gate drained, wait up to --settle seconds (300: the 1-minute average takes
#      minutes to forget a drained load of 50) for the load under --max-load (4);
#   3. still busy -- work goh cannot hold off (an `xctest` started by a Makefile, Xcode,
#      Spotlight): holding on would only block every session's commits, so let go, name the
#      busiest processes, wait --retry seconds (300) holding nothing, and go back to 1;
#   4. past --deadline seconds (14400, 4 h): REFUSE, naming the busiest processes.
#
# A hold blocks every session's commits, so it is SHORT and says how long (BACKLOG 3.2): CMD runs
# for at most --hold seconds (GOH_BENCH_MAX_HOLD, 900), stamped when it starts, which a waiting
# gate prints. Longer than the cap is refused up front -- split it -- and a run that outlives its
# hold fails, named: the gates resumed under it, so its numbers were not taken quiet.
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

max_load=4 settle=300 retry=300 hold="" deadline=14400 label="a measurement" poll="${GOH_BENCH_POLL:-15}"
while [ $# -gt 0 ]; do
    case "$1" in
        --max-load) max_load="${2:?--max-load needs a value}"; shift 2 ;;
        --settle) settle="${2:?--settle needs a value}"; shift 2 ;;
        --retry) retry="${2:?--retry needs a value}"; shift 2 ;;
        --hold) hold="${2:?--hold needs a value}"; shift 2 ;;
        --deadline) deadline="${2:?--deadline needs a value}"; shift 2 ;;
        --label) label="${2:?--label needs a value}"; shift 2 ;;
        --) shift; break ;;
        *) die "quiet.sh: unknown argument $1 (usage: quiet.sh [--max-load N] [--settle S] [--retry R] [--hold H] [--deadline D] -- CMD...)" ;;
    esac
done
[ $# -gt 0 ] || die "quiet.sh: no command to run (usage: quiet.sh [--max-load N] [--settle S] -- CMD...)"

hold="${hold:-$BENCH_LOCK_MAX_HOLD}"
[ "$hold" -le "$BENCH_LOCK_MAX_HOLD" ] ||
    die "quiet.sh: --hold ${hold}s is past the ${BENCH_LOCK_MAX_HOLD}s every other session may wait behind (GOH_BENCH_MAX_HOLD): split the measurement"
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
busiest() { ps -Ao pcpu,comm -r | head -6 >&2 || true; } # head closes early: ps's SIGPIPE is not the verdict
t0="$(date +%s)"
while :; do
    bench_lock_exclusive "$label" || die "quiet.sh: could not hold the host (above)"  # 1. our place
    desktop_lock_acquire "$label"
    t1="$(date +%s)"
    until below "$(load1)" "$max_load"; do                                      # 2. the box, drained
        over "$(elapsed "$t1")" "$settle" && break
        sleep "$(awk -v p="$poll" 'BEGIN { print (p < 5 ? p : 5) }')"
    done
    below "$(load1)" "$max_load" && break
    desktop_lock_release
    bench_lock_release
    if over "$(elapsed "$t0")" "$deadline"; then                                # 4. refuse
        err "the host is not quiet: no window under load $max_load in ${deadline}s, every gate held (load $(load1)); busiest:"
        busiest
        exit 1
    fi
    warn "quiet: load $(load1) with every gate drained for ${settle}s -- not goh's: letting go for ${retry}s; busiest:"
    busiest                                                                      # 3. let go
    sleep "$retry"
done
bench_lock_hold "$hold"
info "quiet: load $(load1) (max $max_load), every gate held for ${hold}s -- running: $*"
t2="$(date +%s)" rc=0
"$@" || rc=$?
if over "$(elapsed "$t2")" "$hold"; then
    err "quiet: the run outlived its ${hold}s hold ($(elapsed "$t2")s): the gates resumed under it, so its numbers were not taken quiet -- split it, or hold longer"
    [ "$rc" -ne 0 ] || rc=1
fi
info "quiet: load at the end $(load1); exit $rc"
exit "$rc"
exit
} # parse-guard
