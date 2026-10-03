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

usage() {
    cat <<'EOF'
usage: local_ci.sh [--step CMD]... [repo-root]

Runs the repo's CI steps and exits nonzero if ANY fail.

  --step CMD   add one shell-command string (repeatable)
  --dry-run    list the steps with their source; execute nothing
  repo-root    project directory (default: $PWD)

Steps also come from GOH_CI_STEPS in <repo-root>/.gatesrc — a colon-separated
command list, e.g.:
  GOH_CI_STEPS='./tools/repo_gates.sh:cargo test:./scripts/lint.sh'
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
ROOT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --step)    [ $# -ge 2 ] || die "--step needs a command argument"
                   CLI_STEPS="${CLI_STEPS}${CLI_STEPS:+:}$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
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
if [ -f "$ROOT/.gatesrc" ]; then
    # shellcheck disable=SC1091
    . "$ROOT/.gatesrc"
fi

SRC_GATESRC="${GOH_CI_STEPS:-}"
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

LOGDIR=$(mktemp -d "${TMPDIR:-/tmp}/goh-local-ci.XXXXXX")
FAILED=0
FAILED_NAMES=""
PROVEN_SKIPPED=0
i=0

# run_step <logf> <cmd-string> — exit code of the step, 124 on timeout.
#
# Two things happen here that did not before, and both are the same defect seen from two sides:
#
#  * the step runs under a CEILING (lib/bounded_run.py), and on expiry it is TERM-then-KILLed in its
#    OWN PROCESS GROUP — not with `pkill -P`, which reaches direct children only and so leaves the
#    test binary's children, i.e. the orphans, running. See the header for the measurement.
#  * the process table is sampled either side of the step, so a child the step leaked is REPORTED
#    rather than inferred from the next run's hang. That is the case the ceiling cannot see: the
#    step exits 0 and the server outlives it.
run_step() {
    local logf="$1" cmd="$2"
    local args=(--grace "${GOH_STEP_GRACE:-5}" --label "$cmd")
    [ "$LCI_LIMIT" -gt 0 ] && args=(--timeout "$LCI_LIMIT" "${args[@]}")
    # </dev/null on the command string: the caller's loop stdin is the step list.
    # shellcheck disable=SC2086  # a command STRING is the feature here; see the header
    python3 "$GOH_ROOT/lib/bounded_run.py" "${args[@]}" -- bash -c "$cmd" >"$logf" 2>&1 </dev/null
}

while IFS="	" read -r src cmd; do
    [ -n "$cmd" ] || continue
    i=$((i + 1))
    logf="$LOGDIR/step-$i.log"
    info "[$i/$_n] ($src) $cmd"
    # The proven-step cache. </dev/null: the loop's stdin is the step list.
    pkey="" ptree=""
    if pair="$(proven_key "$cmd" </dev/null)"; then
        read -r pkey ptree <<<"$pair"
        if hit="$(proven_lookup "$pkey" "$cmd")"; then
            read -r age by <<<"$hit"
            step "proven on this tree $age ago by $by — skipped"
            ok "[$i/$_n] $cmd"
            PROVEN_SKIPPED=$((PROVEN_SKIPPED + 1))
            continue
        fi
    fi
    # Command strings from the repo's own .gatesrc / CLI — shell semantics are
    # the feature (pipelines, env prefixes); the source is repo-local config,
    # the same trust level as every ancestor orchestrator.
    # </dev/null: without it every child inherits the while loop's heredoc
    # stdin — one step that reads stdin (cat, an interactive prompt) swallows
    # the REMAINING step list silently.
    # The canary's "before". Taken here, immediately before the step, so the window is the STEP and
    # not the whole run — a diff taken once at the start of a multi-minute gate cannot say which
    # step leaked anything.
    python3 "$GOH_ROOT/lib/orphan_canary.py" take "$LOGDIR/orphans-$i.json" 2>/dev/null || true
    run_step "$logf" "$cmd"
    rc=$?
    # ...and its verdict, every step, whether or not the step passed. A leak does not care whether
    # the suite was green; that is the entire reason it is silent today.
    orphan_rc=0
    if [ -f "$LOGDIR/orphans-$i.json" ]; then
        python3 "$GOH_ROOT/lib/orphan_canary.py" since "$LOGDIR/orphans-$i.json" --repo "$ROOT" || orphan_rc=$?
        [ "$orphan_rc" -ne 0 ] && FAILED=$((FAILED + 1))
    fi
    if [ "$rc" -eq 0 ] && [ -n "$pkey" ]; then
        # Recorded only if the tree (and the gates) did not move while it ran.
        if [ "$(proven_key "$cmd" </dev/null)" = "$pkey $ptree" ]; then
            proven_record "$pkey" "$ptree" "$cmd" "local_ci" </dev/null \
                || warn "[$i/$_n] could not write the proven record"
        else
            step "the tree or the gates moved while the step ran — not recorded as proven"
        fi
    fi
    if [ "$rc" -eq 0 ] && [ "$orphan_rc" -eq 0 ]; then
        ok "[$i/$_n] $cmd"
    elif [ "$rc" -eq 0 ]; then
        # The step was GREEN and something it spawned outlived it. Named separately, because the
        # common reading of "the test suite passed" is that nothing survived it — and a leaked
        # server is what makes the NEXT suite hang with no output at all.
        err "[$i/$_n] $cmd PASSED, and left processes running (see the canary above)"
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
done <<EOF
$ALL_STEPS
EOF

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
