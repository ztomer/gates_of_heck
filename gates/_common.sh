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
GOH_TMPDIRS=""

# goh_init <gate-name>
goh_init() {
    GOH_NAME="$1"
    # Portable form: BSD mktemp accepts `-t name`, GNU mktemp needs XXXXXX --
    # the BSD form failed every Linux CI run of the structural gate (2026-09-14).
    GOH_LOG="$(mktemp "${TMPDIR:-/tmp}/goh-$GOH_NAME.XXXXXX")"
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
        for _d in $GOH_TMPDIRS; do rm -rf "$_d"; done
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
        # The FAILURES first, then the tail: a 173-test `swift test` puts its
        # nine failing cases far above the last 60 lines, and the log is
        # deleted at exit — a red gate that names no test is a red gate
        # nobody can act on (zinc E.1c, 2026-09-21). GOH_FAIL_PATTERN is
        # the grep; GOH_FAIL_LINES caps how many hits are shown.
        if grep -nE "${GOH_FAIL_PATTERN:-error:|FAILED|failed|panicked at|Assertion|✗}" "$GOH_LOG" \
                | grep -vE "0 failures|passed|failures \(0" | head -n "${GOH_FAIL_LINES:-40}" > "$GOH_LOG.fails" 2>/dev/null \
            && [ -s "$GOH_LOG.fails" ]; then
            printf '%s\n' "── failure lines (grep ${GOH_FAIL_PATTERN:-error:|FAILED|failed|panicked at|Assertion|✗}) ──" >&2
            cat "$GOH_LOG.fails" >&2
            printf '%s\n\n' "── tail ──" >&2
        fi
        rm -f "$GOH_LOG.fails"
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

# goh_index_view <dir>
# Sets GOH_INDEX_VIEW to the version of <dir> a commit would record: the
# INDEX, exported to a temp dir the EXIT trap removes. A whole-tree checker
# handed the working tree at pre-commit scope judges files the commit does
# not contain, and fails both ways: an unstaged edit elsewhere blocks a clean
# commit, and a staged violation passes because its fix is merely unstaged
# (~/.claude/skills, 2026-09-23: concurrent sessions' unstaged edits refused
# a commit of three clean skills). Returns 1 when <dir> lies outside this
# repo: the commit records nothing of it, so there is no index copy to read.
goh_index_view() {
    local dir="$1" top phys rel snap
    GOH_INDEX_VIEW=""
    top="$(cd "$GOH_REPO_ROOT" && pwd -P)" || die "$GOH_NAME: cannot resolve $GOH_REPO_ROOT"
    phys="$(cd "$dir" && pwd -P)" || die "$GOH_NAME: cannot resolve $dir"
    case "$phys/" in
        "$top"/*) ;;
        *) return 1 ;;
    esac
    rel="${phys#"$top"}"
    rel="${rel#/}"
    # Every step is checked by hand: callers use this in an `if`, where
    # `set -e` is off, and an empty $snap would make the prefix `/`.
    snap="$(mktemp -d "${TMPDIR:-/tmp}/goh-index.XXXXXX")" && [ -n "$snap" ] \
        || die "$GOH_NAME: cannot create a temp dir for the index export"
    GOH_TMPDIRS="${GOH_TMPDIRS:+$GOH_TMPDIRS }$snap"
    if ! git -C "$top" ls-files -z -- "${rel:-.}" \
            | git -C "$top" checkout-index -z --stdin --prefix="$snap/"; then
        die "$GOH_NAME: could not export the index under ${rel:-.} — refusing to check the working tree in its place"
    fi
    GOH_INDEX_VIEW="$snap${rel:+/$rel}"
    # A subtree with nothing staged is an EMPTY corpus, not a missing one:
    # the checker's own floor then says so instead of a path error.
    mkdir -p "$GOH_INDEX_VIEW" || die "$GOH_NAME: cannot create $GOH_INDEX_VIEW"
}

# goh_done — the ONLY way a gate earns exit 0 (see the trap in goh_init).
goh_done() {
    GOH_COMPLETED=1
    printf '\n'
    ok "all $GOH_NAME gates passed"
}
