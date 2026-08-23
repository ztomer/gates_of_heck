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
# Prefer a vendored tui/lib.sh in the target repo, else the one shipped here.
GOH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f tui/lib.sh ]; then
    . tui/lib.sh
elif [ -f "$GOH_ROOT/tui/lib.sh" ]; then
    . "$GOH_ROOT/tui/lib.sh"
fi

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

goh_done() { printf '\n'; ok "all $GOH_NAME gates passed"; }
