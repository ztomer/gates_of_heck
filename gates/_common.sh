#!/usr/bin/env bash
# _common.sh — the contract every gate runner in this repo shares.
#
# Sourced, never executed. Gives each gate the same four properties:
#
#   1. Exit non-zero on the FIRST failing check. A gate that keeps going after
#      a failure buries the one line you needed under the ones you didn't.
#   2. On failure, PRINT THE FAILING OUTPUT. This is the whole reason this file
#      exists. The pattern it replaces piped every step to /dev/null and printed
#      "✗ clippy" — a gate that tells you something is wrong and withholds what
#      is a gate you cannot act on.
#   3. Kare icons only (→ · ✓ ✗ ⚠). Emoji are a failure state; see
#      checks/check_no_emoji.py, which enforces exactly that.
#   4. NO_COLOR / non-tty aware, via tui/lib.sh when it is present.
#
# Usage:
#   . "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
#   goh_init "rust"
#   goh_step "fmt" cargo fmt --all -- --check
#   goh_done

set -euo pipefail

# ---- style -----------------------------------------------------------------
# Prefer a vendored tui/lib.sh in the target repo (resolved from the GIT ROOT,
# not the CWD — gates may run from a subdirectory), else the one shipped here.
GOH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GOH_GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
for _goh_tui in "${GOH_GIT_ROOT:+$GOH_GIT_ROOT/tui/lib.sh}" \
                "tui/lib.sh" \
                "$GOH_ROOT/tui/lib.sh"; do
    if [ -n "$_goh_tui" ] && [ -f "$_goh_tui" ]; then
        # shellcheck disable=SC1090
        . "$_goh_tui"
        break
    fi
done
unset _goh_tui

# Fallbacks for every helper we use. Defined unconditionally, then overridden
# by the real lib below — a previous version of this logic defined info/ok/err
# but not warn, so the one branch that called warn died on command-not-found
# under `set -e` instead of warning.
_goh_plain_info() { printf '→ %s\n' "$*"; }
_goh_plain_step() { printf '· %s\n' "$*"; }
_goh_plain_ok()   { printf '✓ %s\n' "$*"; }
_goh_plain_warn() { printf '⚠ %s\n' "$*" >&2; }
_goh_plain_err()  { printf '✗ %s\n' "$*" >&2; }

info() { _goh_plain_info "$@"; }
step() { _goh_plain_step "$@"; }
ok()   { _goh_plain_ok   "$@"; }
warn() { _goh_plain_warn "$@"; }
err()  { _goh_plain_err  "$@"; }

if command -v _lib_info >/dev/null 2>&1; then
    info() { _lib_info "$*"; }
    ok()   { _lib_ok   "$*"; }
    warn() { _lib_warn "$*"; }
    err()  { _lib_err  "$*"; }
fi

die() { err "$*"; exit 1; }

# ---- gate scaffolding ------------------------------------------------------
GOH_NAME=""
GOH_LOG=""
GOH_LOGS=""
GOH_COMPLETED=""

# goh_init <gate-name>
goh_init() {
    GOH_NAME="$1"
    GOH_LOG="$(mktemp -t goh-"$GOH_NAME")"
    # Accumulate: a second goh_init must not orphan the first log.
    GOH_LOGS="${GOH_LOGS:+$GOH_LOGS }$GOH_LOG"
    GOH_COMPLETED=""
    # rc-PRESERVING cleanup trap. The old form, `trap "rm -f '$GOH_LOG'" EXIT`,
    # failed open twice over: (a) double-quoted, so $GOH_LOG expanded at SET
    # time — re-init orphaned the earlier log; (b) the trap's exit status was
    # `rm`'s (0), so abnormal termination (e.g. a syntax error mid-file)
    # exited 0 — a broken gate committed green.
    #
    # Preserving $? alone is NOT enough on bash 3.2: when the script dies of a
    # PARSE error while `set -e` is active (this file sets it), bash delivers
    # $?=0 to the trap. So zero is only honored if goh_done ran; a 0 that
    # arrives without completion is re-raised as a failure. Probed on
    # 3.2.57: parse-error trap sees rc=2 with set +e, rc=0 with set -e.
    # Note: goh_init owns the EXIT trap. A consumer needing its own must
    # install it AFTER goh_init and preserve $? the same way.
    trap '
        _goh_rc=$?
        for _l in $GOH_LOGS; do rm -f "$_l"; done
        if [ "$_goh_rc" -eq 0 ] && [ "$GOH_COMPLETED" != "1" ]; then
            err "$GOH_NAME: exited 0 without completing — abnormal termination is never a pass"
            _goh_rc=1
        fi
        exit "$_goh_rc"' EXIT
    printf '\n== %s gate ==\n' "$GOH_NAME"
}

# goh_step <label> <command...>
# Runs the command with output captured. On failure: dump the tail, then exit.
# GOH_TIME=1 appends per-step elapsed whole seconds to the ok line — the
# ornament that would have caught the 15s disk-hygiene cost without hand
# timing. Off by default; zero overhead otherwise.
goh_step() {
    local label="$1"; shift
    local _goh_t0
    _goh_t0=$(date +%s)
    step "$label"
    if ! "$@" >"$GOH_LOG" 2>&1; then
        printf '\n'
        tail -n "${GOH_TAIL:-60}" "$GOH_LOG" >&2
        printf '\n'
        die "$GOH_NAME: $label failed (command: $*)"
    fi
    if [ -n "${GOH_TIME:-}" ]; then
        ok "$label ($(( $(date +%s) - _goh_t0 ))s)"
    else
        ok "$label"
    fi
}

# goh_optional_step <label> <file-that-must-exist> <command...>
# For checks that only apply when the repo opted into them.
goh_optional_step() {
    local label="$1" guard="$2"; shift 2
    if [ ! -e "$guard" ]; then
        warn "$label skipped — $guard not present"
        return 0
    fi
    goh_step "$label" "$@"
}

# goh_step_in <dir> <label> <command...>
# Run a command inside <dir> without shell-string interpolation: argv stays
# argv, so paths with spaces or quotes cannot become code. The replaced
# pattern was `env sh -c "cd '$dir' && ... $RUN ..."` — a quoting bug waiting
# on the first path that contains a single quote.
goh_step_in() {
    local dir="$1" label="$2"; shift 2
    if [ "$dir" != "$PWD" ]; then
        goh_step "$label" /usr/bin/env bash -c 'cd "$1" && exec "${@:2}"' _ "$dir" "$@"
    else
        goh_step "$label" "$@"
    fi
}

# goh_done — the ONLY way a gate earns exit 0 (see the trap in goh_init).
goh_done() {
    GOH_COMPLETED=1
    printf '\n'
    ok "all $GOH_NAME gates passed"
}
