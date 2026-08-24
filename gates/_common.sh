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

# goh_init <gate-name>
goh_init() {
    GOH_NAME="$1"
    GOH_LOG="$(mktemp -t goh-"$GOH_NAME")"
    # shellcheck disable=SC2064  # expand GOH_LOG now, not at trap time
    trap "rm -f '$GOH_LOG'" EXIT
    printf '\n== %s gate ==\n' "$GOH_NAME"
}

# goh_step <label> <command...>
# Runs the command with output captured. On failure: dump the tail, then exit.
goh_step() {
    local label="$1"; shift
    step "$label"
    if ! "$@" >"$GOH_LOG" 2>&1; then
        printf '\n'
        tail -n "${GOH_TAIL:-60}" "$GOH_LOG" >&2
        printf '\n'
        die "$GOH_NAME: $label failed (command: $*)"
    fi
    ok "$label"
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

goh_done() { printf '\n'; ok "all $GOH_NAME gates passed"; }
