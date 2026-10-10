#!/usr/bin/env bash
# local_ci.sh — ONE declarative step runner replacing the ~10 copy-pasted
# local-CI orchestrators (app_updates tools/local_ci.sh, CadGoose2
# tools/local_ci.sh, divoom-control scripts/ci_local.sh all share the same
# skeleton: a step list, a fail accumulator, log-on-failure, tui styling).
# Only the step LIST was per-repo; the skeleton drifted copy by copy. The list
# is data here, declared by the repo:
#
#   .gatesrc:  GOH_CI_STEPS='./tools/repo_gates.sh:cargo test:./scripts/lint.sh'
# or CLI:    local_ci.sh --step './tools/repo_gates.sh' --step 'cargo test'
# (both at once: .gatesrc steps run first, then --step additions)
#
# Each token is one shell command string. It runs with output captured to a
# temp-dir log; on failure the log tail is printed and the accumulator ticks.
# A failing step NEVER stops the run — one pass must surface every failure,
# which is what every ancestor's fail-accumulator existed for. Exit is nonzero
# iff ANY step failed. --dry-run lists steps (with their source) and runs none.
#
# Deliberately thin: labels are the commands themselves, there is no step
# metadata to drift. The value is that the skeleton exists exactly once.
#
# PROVEN STEPS. Every step goes through the proven-step cache (gates/_proven.sh,
# the rationale in gates/proven.sh): a step that already passed on this exact
# committed tree -- in the pre-commit hook, or an earlier run -- is skipped with
# a line saying who proved it and when, and a green step on a clean tree leaves
# a record. A dirty working tree has no key, so everything runs as before.
# GOH_PROVEN=0 turns the cache off.
#
# Exit codes: 0 all green · 1 any step failed · 2 usage/config error.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOH_ROOT="$(cd "$HERE/.." && pwd)"

for _tui in "${GOH_GIT_ROOT:-}/tui/lib.sh" tui/lib.sh "$GOH_ROOT/tui/lib.sh"; do
    # shellcheck disable=SC1090  # vendored lib resolved at runtime, as _common.sh
    [ -n "$_tui" ] && [ -f "$_tui" ] && . "$_tui" && break
done
unset _tui
info() { printf '→ %s\n' "$*"; }
ok() { printf '✓ %s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*" >&2; }
err() { printf '✗ %s\n' "$*" >&2; }
command -v _lib_info >/dev/null 2>&1 && {
    info() { _lib_info "$*"; }; ok() { _lib_ok "$*"; }
    warn() { _lib_warn "$*"; }; err() { _lib_err "$*"; };
}
die() { err "local_ci: $*"; exit "${2:-2}"; }
# shellcheck source=gates/_proven.sh
. "$HERE/_proven.sh"
# shellcheck source=gates/_goh_bin.sh
. "$HERE/_goh_bin.sh"
# shellcheck source=lib/bench_lock.sh
. "$GOH_ROOT/lib/bench_lock.sh"
bench_lock_join "local ci" # a measurement (tools/quiet.sh) waits for this run to finish
# Resolved ONCE, exported: every step runs under `goh canary` (below), and any gate a step runs
# (rust_gate, structural) takes the same answer instead of resolving again.
goh_resolve_native

usage() {
    cat <<'EOF'
usage: local_ci.sh [--step CMD]... [--jobs N] [--steps-only] [--dry-run] [repo-root]

Runs the repo's CI steps and exits nonzero if ANY fail.

  --step CMD   add one shell-command string (repeatable)
  --dry-run    list the steps with their source; execute nothing
  --jobs N     run up to N steps at once (beats GOH_CI_JOBS)
  --steps-only run the --step steps alone, not .gatesrc's GOH_CI_STEPS
  repo-root    project directory (default: $PWD)

Steps also come from GOH_CI_STEPS in <repo-root>/.gatesrc — a colon-separated
command list, e.g.:
  GOH_CI_STEPS='./tools/repo_gates.sh:cargo test:./tools/lint.sh'
The separator is a bare colon split: keep colons OUT of step strings —
parameter expansions like ${VAR:+flag} contain one and will be cut. Put such
logic in a small repo script and invoke that as the step.
.gatesrc steps run first, then --step ones.
GOH_LCI_TIMEOUT=N caps each step at N seconds (exit 124, named); the default is 900.
means no limit.
EOF
}

DRY_RUN=0
CLI_STEPS=""
CLI_JOBS=""
STEPS_ONLY=0
ROOT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --step)    [ $# -ge 2 ] || die "--step needs a command argument"
                   CLI_STEPS="${CLI_STEPS}${CLI_STEPS:+:}$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --jobs)    [ $# -ge 2 ] || die "--jobs needs a count"
                   CLI_JOBS="$2"; shift 2 ;;
        --steps-only) STEPS_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        --*)       die "unknown option: $1 (see --help)" ;;
        *)         ROOT="$1"; shift ;;
    esac
done

[ -n "$ROOT" ] || ROOT="$PWD"
[ -d "$ROOT" ] || die "repo root is not a directory: $ROOT"
cd "$ROOT"
ROOT="$(pwd)"

# Per-repo declaration. Sourced like every other gate reads .gatesrc — it may
# carry other GOH_* settings; only GOH_CI_STEPS concerns this script.
# THE ENVIRONMENT IS NOT CONFIGURATION: a key is the repo's iff its own file says so (rust_gate.sh
# and structural.sh drop theirs the same way). Measured: a run that exported GOH_CI_JOBS=4 made
# every local_ci its test suite started concurrent, and an order test went red -- the parent's
# budget had become every nested repo's. GOH_CI_STEPS leaked the same way; --step and --jobs are
# how a caller says what it means.
unset GOH_CI_STEPS GOH_CI_JOBS 2>/dev/null || true
if [ -f "$ROOT/.gatesrc" ]; then
    # shellcheck disable=SC1091
    . "$ROOT/.gatesrc"
fi

SRC_GATESRC="${GOH_CI_STEPS:-}"
# A caller that schedules FOR the repo (rust_gate.sh --each-crate) passes its own steps and budget:
# --steps-only drops the repo's GOH_CI_STEPS, --jobs beats its GOH_CI_JOBS.
[ "$STEPS_ONLY" -eq 1 ] && SRC_GATESRC=""
[ -n "$CLI_JOBS" ] && GOH_CI_JOBS="$CLI_JOBS"
_proven_err="$(proven_settings 2>&1)" || die "$_proven_err"
proven_settings

# GOH_LCI_TIMEOUT: per-step wall-clock ceiling in seconds. The DEFAULT is 900 (15 min), and the
# default is the point: this was unset everywhere in the estate, which means "no limit", which means
# a hung `cargo test` sat there until something killed it -- and the thing that killed it (a timeout,
# a cancelled agent) killed the run BEFORE it could report anything, so the only observable was
# silence. media_server 2026-10-03: nine orphans, each holding the cargo build lock, every later
# `cargo test` blocked, and nothing anywhere said why. A ceiling nobody gave a number is a
# suggestion. 900 is set from measurement, not taste: this estate's own slowest test step is
# mediaops' rust gate at 168 s and the whole pytest suite is 76 s, so 900 is 5x the slowest thing
# measured and a fifteenth of the day it cost.
#
# `0` is the explicit escape hatch and now has to be typed.
#
# THE SWEEP IS THE OTHER HALF, and it was measured wrong. The old sweep was `pkill -P "$pid"`,
# DIRECT children only, on a step shaped `cargo test` -> test binary -> the server the test spawned.
# Measured 2026-10-03 with `bash -c 'sleep 400 & wait'`: the old sweep left 2 grandchildren ALIVE;
# `lib/bounded_run.py` (own session + killpg) leaves NOTHING. So the mechanism meant to unstick a
# hung step left running exactly the processes that make the NEXT step hang.
case "${GOH_LCI_TIMEOUT:-900}" in
    ""|0) LCI_LIMIT=0 ;;
    *[!0-9]*)
        die "GOH_LCI_TIMEOUT must be a non-negative integer of seconds (got '${GOH_LCI_TIMEOUT}')" ;;
    # `:-900` on BOTH reads, not just the subject. The case matched the defaulted value and the arm
    # then read the bare name, so a repo that never set the key died on an unbound variable under
    # `set -u` -- the default being on is what made it reachable. (The key was unset in every repo
    # in the estate until now, and the branch was simply never taken.)
    *) LCI_LIMIT="${GOH_LCI_TIMEOUT:-900}" ;;
esac

if [ -z "$SRC_GATESRC" ] && [ -z "$CLI_STEPS" ]; then
    err "local_ci: no steps declared"
    err "  declare them in $ROOT/.gatesrc as:  GOH_CI_STEPS='cmd1:cmd2'"
    err "  or pass them on the command line:   local_ci.sh --step 'cmd1'"
    exit 2
fi

LOGDIR=""
# shellcheck disable=SC2329  # invoked via the EXIT trap below
# A PASSING run's logs are noise; a FAILING run's are the only record of why.
# Deleting them on failure takes with them the one thing needed to reproduce a
# shuffled test failure -- its seed -- which the tail below is too short to
# have shown. Kept on failure, and the path is printed with the result.
cleanup() {
    if [ -n "$LOGDIR" ] && [ "${FAILED:-0}" -eq 0 ]; then
        rm -rf "$LOGDIR" || true
    fi
    if [ -n "$BENCH_LOCK_ENTRY" ]; then rm -f "$BENCH_LOCK_ENTRY"; fi
}
trap cleanup EXIT

ALL_STEPS=""      # newline-joined "source<TAB>cmd" records
_n=0
_add() { # _add <source> <cmd>
    ALL_STEPS="${ALL_STEPS}${ALL_STEPS:+
}$1	$2"
}
if [ -n "$SRC_GATESRC" ]; then
    _rest="$SRC_GATESRC"
    while [ -n "$_rest" ]; do
        _tok="${_rest%%:*}"
        _rest="${_rest#*:}"
        [ -n "$_tok" ] && { _n=$((_n + 1)); _add ".gatesrc" "$_tok"; }
        [ "$_tok" = "$_rest" ] && break
    done
fi
if [ -n "$CLI_STEPS" ]; then
    _rest="$CLI_STEPS"
    while [ -n "$_rest" ]; do
        _tok="${_rest%%:*}"
        _rest="${_rest#*:}"
        [ -n "$_tok" ] && { _n=$((_n + 1)); _add "--step" "$_tok"; }
        [ "$_tok" = "$_rest" ] && break
    done
fi

if [ "$_n" -eq 0 ]; then
    err "local_ci: no steps declared"
    err "  declare them in $ROOT/.gatesrc as:  GOH_CI_STEPS='cmd1:cmd2'"
    err "  or pass them on the command line:   local_ci.sh --step 'cmd1'"
    exit 2
fi

section "local CI — $_n step(s) in $ROOT"

if [ "$DRY_RUN" -eq 1 ]; then
    while IFS="	" read -r src cmd; do
        [ -n "$cmd" ] || continue
        step "[$src] $cmd"
    done <<EOF
$ALL_STEPS
EOF
    ok "dry-run: nothing executed"
    exit 0
fi

# A red run KEEPS its logs (the failure names them), so they were unbounded: 619 in one temp dir
# (ZoneWM, 2026-10-06). Kept now: the newest 20, and anything touched within the hour -- a live
# run's dir is written as it runs, so a concurrent run is never pruned.
python3 -S "$GOH_ROOT/lib/prune_kept.py" "${TMPDIR:-/tmp}" goh-local-ci. 2>/dev/null || true
# The backstop for what a KILLED run could not remove (a trap does not run on SIGKILL): any of our
# own `goh-*` entries untouched for 12 hours. Every gate's own files are removed at its exit
# (goh_cleanup_add, tests/test_gate_temp_leaks.py); this bounds the rest.
python3 -S "$GOH_ROOT/lib/prune_kept.py" "${TMPDIR:-/tmp}" --backstop 2>/dev/null || true
LOGDIR=$(mktemp -d "${TMPDIR:-/tmp}/goh-local-ci.XXXXXX")
FAILED=0
FAILED_NAMES=""
PROVEN_SKIPPED=0

# run_step <logf> <cmd-string> <index> — exit code of the step, 124 on a timeout. Background only:
# it EXECs, so it replaces the shell that runs it.
#
# ONE call, because the three things this step needs are one thing and cannot drift apart:
#
#  * a CEILING. On expiry the step is TERM-then-KILLed in its OWN PROCESS GROUP — not with
#    `pkill -P`, which reaches direct children only and so leaves the test binary's children, i.e.
#    the orphans, running. Measured on the header's shape; see it there.
#  * the step's OUTPUT, captured to <logf> with stderr folded in, exactly as before.
#  * a LEAK REPORT for whatever is still in the step's process group when it exits. This is the case
#    the ceiling cannot see: the step exits 0 and the server outlives it, and the only evidence is a
#    LATER run that blocks. lib/orphan_canary.py owns it; --log keeps the evidence beside the step's
#    output so a red push has both. Judged by PROCESS GROUP, so concurrent steps (GOH_CI_JOBS) never
#    report each other's processes.
run_step() {
    local logf="$1" cmd="$2" idx="$3"
    local args=(--repo "$ROOT" --log "$logf" --grace "${GOH_STEP_GRACE:-5}" --label "$cmd"
                --snapshot "$LOGDIR/orphans-$idx.json")
    [ "$LCI_LIMIT" -gt 0 ] && args=(--timeout "$LCI_LIMIT" "${args[@]}")
    # </dev/null on the command string: the caller's loop stdin is the step list. EXEC: run_step is
    # started with `&`, which forks a subshell for the function, and a TERM to that subshell ended it
    # without reaching the wrapper -- so the step's sweep never ran. Exec'd, $! IS the wrapper.
    # shellcheck disable=SC2086  # a command STRING is the feature here; see the header
    # The native canary (`goh canary`, the same contract: tests/test_orphan_canary.py runs both) when
    # the binary resolved -- one exec per step instead of a Python start-up -- else the Python.
    if [ -x "${GOH_RESOLVED_BIN:-}" ]; then
        exec "$GOH_RESOLVED_BIN" canary "${args[@]}" -- bash -c "$cmd" </dev/null
    fi
    exec python3 -S "$GOH_ROOT/lib/orphan_canary.py" wrap "${args[@]}" -- bash -c "$cmd" </dev/null
}

# THE SCHEDULER (BACKLOG P2). GOH_CI_JOBS=N runs up to N steps at once; unset is 1, i.e. serial,
# because concurrency is a repo's DECLARATION, never an inference -- two steps that write one file
# (the Finance `*.out` class) are only safe apart, and nothing here can see that. What a repo
# declares beside the count is the RESOURCE TAG: `[db,screen] cmd` -- steps sharing a tag never
# overlap. (A tag cannot hold a colon: the colon separates steps. Write `cargo=crates/x`.)
#
# Reports come out in DECLARED order whatever order the steps finish in, so a concurrent run reads
# exactly like a serial one; the fail accumulator and the exit code do not change. Serial runs print
# each step's header BEFORE it runs (a live progress line); concurrent ones print it with the report.
# Completions arrive on a FIFO -- bash 3.2 has no `wait -n`, and polling would charge every step.
case "${GOH_CI_JOBS:-1}" in
    *[!0-9]*|0|"") die "GOH_CI_JOBS must be a positive integer of concurrent steps (got '${GOH_CI_JOBS}')" ;;
    *) JOBS="${GOH_CI_JOBS:-1}" ;;
esac
LIVE=0; [ "$JOBS" -eq 1 ] && LIVE=1

n=0
while IFS="	" read -r src cmd; do
    [ -n "$cmd" ] || continue
    n=$((n + 1))
    SRC[n]="$src"; CMD[n]="$cmd"; STATE[n]=pending; TAGS[n]=""
    case "$cmd" in
        "["*"] "*) _t="${cmd%%] *}"; TAGS[n]=",${_t#[},"
                   case "${TAGS[n]}" in *[!A-Za-z0-9_.=/,-]*) die "step $n: a tag holds letters, digits and _.=/- only (got '[${_t#[}]')" ;; esac
                   RUN[n]="${cmd#*] }" ;;
        *) RUN[n]="$cmd" ;;
    esac
done <<EOF
$ALL_STEPS
EOF

# A STOPPED run stops its steps. Each runs in the background under its own wrapper, so a TERM to
# this script used to end the script alone and leave every running step going, unowned
# (tests/test_bounded_run_interrupt.py). The wrapper sweeps its step's group on TERM; this hands
# the TERM on. (A background job of a non-job-control shell ignores SIGINT from birth, and an
# ignored-at-entry signal cannot be trapped -- so INT is turned into TERM here, not relied on there.)
_stop() {
    local i
    trap - TERM INT HUP
    for ((i = 1; i <= n; i++)); do
        [ "${STATE[i]:-}" = running ] && kill -TERM "${WRAP[i]:-}" 2>/dev/null
    done
    wait
    FAILED=$((FAILED + 1))
    err "local_ci: stopped by $1 -- every running step was stopped with it"
    exit "$2"
}
trap '_stop TERM 143' TERM
trap '_stop INT 130' INT
trap '_stop HUP 129' HUP

HELD=","        # the tags of the steps running now, comma-delimited
RUNNING=0
mkfifo "$LOGDIR/done"
exec 3<>"$LOGDIR/done"
# Every write and read goes through fd 3, so the name is not needed past
# here -- and the directory is handed to the operator to search on failure,
# where a FIFO makes `grep -r` block forever (tests/test_local_ci.py).
rm -f "$LOGDIR/done"

_tags_free() { # _tags_free <i> -- none of step i's tags is held
    local t rest="${TAGS[$1]#,}"
    while [ -n "$rest" ]; do
        t="${rest%%,*}"; rest="${rest#*,}"
        case "$HELD" in *",$t,"*) return 1 ;; esac
    done
    return 0
}

# _start <i> -- a proven hit is done at once; anything else is launched in the background.
_start() {
    local i="$1" pair hit age by
    PKEY[i]="" PTREE[i]=""
    [ "$LIVE" -eq 1 ] && info "[$i/$_n] (${SRC[i]}) ${CMD[i]}"
    if pair="$(proven_key "${CMD[i]}" </dev/null)"; then
        read -r "PKEY[i]" "PTREE[i]" <<<"$pair"
        if hit="$(proven_lookup "${PKEY[i]}" "${CMD[i]}")"; then
            read -r age by <<<"$hit"
            HIT[i]="$age $by"; STATE[i]=done; RC[i]=0
            [ -z "${GOH_TIMINGS:-}" ] \
                || python3 "$GOH_ROOT/lib/step_timings.py" record "${CMD[i]}" 0 0 step hit </dev/null
            return 0
        fi
    fi
    HIT[i]=""; STATE[i]=running; RUNNING=$((RUNNING + 1))
    HELD="${HELD}${TAGS[i]#,}"
    # Command strings from the repo's own .gatesrc / CLI — shell semantics are the feature
    # (pipelines, env prefixes); the source is repo-local config, the same trust level as every
    # ancestor orchestrator. </dev/null: a step that reads stdin gets EOF, never the step list.
    # The wrapper is the subshell's child; the subshell hands a TERM on to it (see _stop).
    ( trap 'kill -TERM "$wrap" 2>/dev/null; wait "$wrap"; exit 143' TERM
      run_step "$LOGDIR/step-$i.log" "${RUN[i]}" "$i" &
      wrap=$!
      wait "$wrap"
      echo "$i $?" >&3 ) </dev/null &
    WRAP[i]=$!
}

_launch() { # start every pending step a slot and its tags allow, in declared order
    local i
    for ((i = 1; i <= n; i++)); do
        [ "${STATE[i]}" = pending ] || continue
        [ "$RUNNING" -lt "$JOBS" ] || return 0
        _tags_free "$i" && _start "$i"
        # Serial: a run is reported before the next starts, so the live header stays on top.
        [ "$LIVE" -eq 1 ] && [ "$RUNNING" -gt 0 ] && return 0
    done
    return 0
}

_report() { # _report <i>
    local i="$1" rc="${RC[$1]}" cmd="${CMD[$1]}" logf="$LOGDIR/step-$1.log" age by
    [ "$LIVE" -eq 1 ] || info "[$i/$_n] (${SRC[i]}) $cmd"
    if [ -n "${HIT[i]}" ]; then
        read -r age by <<<"${HIT[i]}"
        step "proven on this tree $age ago by $by — skipped"
        ok "[$i/$_n] $cmd"
        PROVEN_SKIPPED=$((PROVEN_SKIPPED + 1))
        return 0
    fi
    if [ "$rc" -eq 0 ] && [ -n "${PKEY[i]}" ]; then
        # Recorded only if the tree (and the gates) did not move while it ran.
        if [ "$(proven_key "$cmd" </dev/null)" = "${PKEY[i]} ${PTREE[i]}" ]; then
            proven_record "${PKEY[i]}" "${PTREE[i]}" "$cmd" "local_ci" </dev/null \
                || warn "[$i/$_n] could not write the proven record"
        else
            step "the tree or the gates moved while the step ran — not recorded as proven"
        fi
    fi
    if [ "$rc" -eq 0 ]; then
        ok "[$i/$_n] $cmd"
    elif [ "$rc" -eq 125 ]; then
        # The step was GREEN and something it spawned outlived it. Named separately, and with its own
        # exit code, because the common reading of "the test suite passed" is that nothing survived
        # it — and a leaked server is what makes the NEXT suite hang with no output at all.
        err "[$i/$_n] PASSED, and left processes running: $cmd"
        err "  Wrap the child in a guard that reaps it on a panic — the pids are named above, and"
        err "  goh unreaped-spawn (gates/goh.sh) is the gate that keeps the shape out."
        FAILED=$((FAILED + 1))
        FAILED_NAMES="$FAILED_NAMES
  $cmd (orphans)"
    elif [ "$rc" -eq 124 ]; then
        err "[$i/$_n] TIMED OUT after ${LCI_LIMIT}s: $cmd"
        warn "  The ceiling is GOH_LCI_TIMEOUT (default ${GOH_LCI_TIMEOUT:-900}s). A step that needs"
        warn "  longer must say so in .gatesrc — an unbounded step cannot be told apart from a hang."
        warn "--- output (tail ${GOH_TAIL:-30}; full log kept, path below) ---"
        tail -n "${GOH_TAIL:-30}" "$logf" >&2
        FAILED=$((FAILED + 1))
        FAILED_NAMES="$FAILED_NAMES
  $cmd (timeout)"
    else
        err "[$i/$_n] FAILED: $cmd"
        warn "--- output (tail ${GOH_TAIL:-30}; full log kept, path below) ---"
        tail -n "${GOH_TAIL:-30}" "$logf" >&2
        FAILED=$((FAILED + 1))
        FAILED_NAMES="$FAILED_NAMES
  $cmd"
    fi
}

NEXT=1
_launch
while [ "$NEXT" -le "$n" ]; do
    while [ "$NEXT" -le "$n" ] && [ "${STATE[NEXT]}" = done ]; do
        _report "$NEXT"; NEXT=$((NEXT + 1))
    done
    [ "$NEXT" -le "$n" ] || break
    if [ "$RUNNING" -eq 0 ]; then _launch; continue; fi
    read -r -u 3 _i _rc
    wait "${WRAP[_i]}" 2>/dev/null || true
    RC[_i]="$_rc"; STATE[_i]=done; RUNNING=$((RUNNING - 1))
    _rest="${TAGS[_i]#,}"
    while [ -n "$_rest" ]; do
        _t="${_rest%%,*}"; _rest="${_rest#*,}"
        HELD="${HELD/,$_t,/,}"
    done
    _launch
done
exec 3>&-

section "result"
if [ "$FAILED" -eq 0 ]; then
    if [ "$PROVEN_SKIPPED" -gt 0 ]; then
        ok "all $_n step(s) passed ($PROVEN_SKIPPED proven earlier on this tree, not re-run)"
    else
        ok "all $_n step(s) passed"
    fi
    exit 0
fi
err "$FAILED of $_n step(s) failed:"
err "$FAILED_NAMES"
# The tail above is 30 lines. A shuffled test suite prints its seed at the
# TOP, so the line that says how to reproduce the failure is never in it.
err ""
err "  full output: $LOGDIR"
exit 1
exit
} # parse-guard
