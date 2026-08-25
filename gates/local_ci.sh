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
.gatesrc steps run first, then --step ones.
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
ROOT="$(cd "$ROOT" && pwd)"

# Per-repo declaration. Sourced like every other gate reads .gatesrc — it may
# carry other GOH_* settings; only GOH_CI_STEPS concerns this script.
if [ -f "$ROOT/.gatesrc" ]; then
    # shellcheck disable=SC1091
    . "$ROOT/.gatesrc"
fi

SRC_GATESRC="${GOH_CI_STEPS:-}"

if [ -z "$SRC_GATESRC" ] && [ -z "$CLI_STEPS" ]; then
    err "local_ci: no steps declared"
    err "  declare them in $ROOT/.gatesrc as:  GOH_CI_STEPS='cmd1:cmd2'"
    err "  or pass them on the command line:   local_ci.sh --step 'cmd1'"
    exit 2
fi

LOGDIR=""
# shellcheck disable=SC2329  # invoked via the EXIT trap below
cleanup() { [ -n "$LOGDIR" ] && rm -rf "$LOGDIR" || true; }
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
i=0

while IFS="	" read -r src cmd; do
    [ -n "$cmd" ] || continue
    i=$((i + 1))
    logf="$LOGDIR/step-$i.log"
    info "[$i/$_n] ($src) $cmd"
    # Command strings from the repo's own .gatesrc / CLI — shell semantics are
    # the feature (pipelines, env prefixes); the source is repo-local config,
    # the same trust level as every ancestor orchestrator.
    if bash -c "$cmd" >"$logf" 2>&1; then
        ok "[$i/$_n] $cmd"
    else
        err "[$i/$_n] FAILED: $cmd"
        warn "--- output (tail ${GOH_TAIL:-30}) ---"
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
    ok "all $_n step(s) passed"
    exit 0
fi
err "$FAILED of $_n step(s) failed:"
err "$FAILED_NAMES"
exit 1
