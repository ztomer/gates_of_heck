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
# Consumers load HEAD's copy of this file, never the shared working tree (C4, _from_head.sh).
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh" && goh_from_head_lib "${BASH_SOURCE[0]}" && return 0

set -euo pipefail

# ---- style -----------------------------------------------------------------
# Prefer a vendored tui/lib.sh in the target repo (resolved from the GIT ROOT,
# not the CWD — gates may run from a subdirectory), else the one shipped here.
GOH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Every Python a gate runs finds tui.lib from HERE, never from the caller's shell: ~/.zshrc exported
# it, and non-interactive shells never read ~/.zshrc (ZoneWM, 2026-10-05). The gate knows where it
# lives; making every consumer re-export it was the workaround (tests/test_gate_runtime_path.py).
export PYTHONPATH="$GOH_ROOT${PYTHONPATH:+:$PYTHONPATH}"
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
# The working tree's stamp (lib/tree_stamp.py), taken by goh_tree_stamp. A
# gate that BUILDS AND TESTS the working tree certifies the bytes that were
# there while it ran; an edit mid-run made one die in an untouched target with
# no cause named (ZoneWM 2c.9, 2026-09-21). So a green run over a tree that
# moved is refused in goh_done, and a red one names the move in the trap.
GOH_TREE_STAMP_FILE=""
GOH_TREE_CHECKED=""

# goh_tree_stamp — call right after goh_init in a gate that builds and tests
# the working tree (swift, rust, python). NOT in the staged gates: they judge
# the INDEX, so an edit elsewhere in the tree does not touch their verdict,
# and refusing a commit because another session edited an unrelated file
# would be the gate being wrong. A no-op outside a git repo, and once per run.
goh_tree_stamp() {
    [ -z "$GOH_TREE_STAMP_FILE" ] && [ -n "$GOH_GIT_ROOT" ] || return 0
    GOH_TREE_STAMP_FILE="$(mktemp "${TMPDIR:-/tmp}/goh-stamp.XXXXXX")"
    python3 "$GOH_ROOT/lib/tree_stamp.py" take "$GOH_GIT_ROOT" "$GOH_TREE_STAMP_FILE" \
        || die "$GOH_NAME: cannot stamp the tree at $GOH_GIT_ROOT"
}

# goh_tree_check — 0 if the tree is where goh_tree_stamp found it (or none was
# taken); else prints the moved paths and returns 1.
goh_tree_check() {
    [ -n "$GOH_TREE_STAMP_FILE" ] || return 0
    GOH_TREE_CHECKED=1
    python3 "$GOH_ROOT/lib/tree_stamp.py" check "$GOH_GIT_ROOT" "$GOH_TREE_STAMP_FILE" --name "$GOH_NAME"
}

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
        # A red run names the move, if there was one: that is its likeliest cause.
        if [ "$_goh_rc" -ne 0 ] && [ -z "$GOH_TREE_CHECKED" ]; then goh_tree_check || true; fi
        [ -n "$GOH_TREE_STAMP_FILE" ] && rm -f "$GOH_TREE_STAMP_FILE"
        for _l in $GOH_LOGS; do rm -f "$_l"; done
        for _d in $GOH_TMPDIRS; do rm -rf "$_d"; done
        if [ "$_goh_rc" -eq 0 ] && [ "$GOH_COMPLETED" != "1" ]; then
            err "$GOH_NAME: exited 0 without completing — abnormal termination is never a pass"
            _goh_rc=1
        fi
        exit "$_goh_rc"' EXIT
    printf '\n== %s gate ==\n' "$GOH_NAME"
}

# goh_step_timeout <label> -- prints the ceiling in seconds, or 0 for none.
#
# A step with no ceiling is a gate that cannot tell a slow step from a hung one, and this file's
# steps run BUILD AND TEST commands: `cargo test`, `swift test`, `pytest`. `gates/local_ci.sh` has
# had a ceiling since it was written; `goh_step` did not, so the same hang was bounded in one path
# and unbounded in the other. That is not two policies, it is the absence of one.
#
# DEFAULT 1800 for a goh_step (and 900 for a local_ci step (see local_ci.sh for why that half is
# lower), both set from measurement rather than taste: this estate's slowest measured single step is
# mediaops' rust gate at 168 s, and a cold `swift test`/`cargo build --all-targets` is minutes. The
# point is not the number -- it is that it EXISTS, is PRINTED on every run, and that exceeding it
# says "TIMED OUT after Ns" with the survivors named instead of dying silently.
#
# GOH_STEP_TIMEOUT=0 opts out, and has to be typed. A non-numeric value is a config error naming the
# value: silently ignoring a typo'd ceiling would leave the step unbounded, which is the defect.
# Lower-case on purpose: a `GOH_*` name here reads as a repo-settable config key to
# `test_config_schema.py`, and this one is not one -- it is the constant the default
# comes from. GOH_STEP_TIMEOUT is the key; this is what it falls back to.
_goh_step_default_timeout=1800
goh_step_timeout() {
    case "${GOH_STEP_TIMEOUT:-$_goh_step_default_timeout}" in
        ""|0) echo 0 ;;
        *[!0-9]*)
            err "${GOH_NAME:-gate}: GOH_STEP_TIMEOUT must be a non-negative integer of seconds (got '${GOH_STEP_TIMEOUT}') — refusing to run a step with an unknown ceiling"
            exit 2 ;;
        *) echo "${GOH_STEP_TIMEOUT:-$_goh_step_default_timeout}" ;;
    esac
}

# goh_step <label> <command...>
# Runs the command with output captured. On failure: dump the tail, then exit.
# GOH_TIME=1 appends per-step elapsed whole seconds to the ok line — the
# ornament that would have caught the 15s disk-hygiene cost without hand
# timing. Off by default; zero overhead otherwise.
#
# Under a CEILING, by default. The step runs as its own process group (lib/bounded_run.py), so on
# expiry the sweep reaches the whole subtree rather than the direct children -- measured 2026-10-03:
# `pkill -P` left two grandchildren alive against `bash -c 'sleep 400 & wait'`, and those are the
# processes that make the NEXT run hang.
goh_step() {
    local label="$1"; shift
    local _goh_t0 _goh_rc=0 _goh_limit
    _goh_t0=$(date +%s)
    _goh_limit="$(goh_step_timeout)"
    # `if cmd; then rc=0; else rc=$?; fi` and NEVER `if ! cmd; then rc=$?`. In the negated form `$?`
    # is the status of the `!` — 0 when the command FAILED — so a step that died came back
    # successful and the gate ran on. Caught by tests/test_tui_integration.py
    # (`failing step's output was withheld`), which is the contract this line exists to keep.
    if [ "$_goh_limit" -gt 0 ]; then
        step "$label (≤${_goh_limit}s)"
        if python3 -S "$GOH_ROOT/lib/bounded_run.py" --timeout "$_goh_limit" \
                --grace "${GOH_STEP_GRACE:-5}" --label "$label" -- "$@" >"$GOH_LOG" 2>&1; then
            _goh_rc=0
        else
            _goh_rc=$?
        fi
    else
        step "$label (UNBOUNDED — GOH_STEP_TIMEOUT=0)"
        if "$@" >"$GOH_LOG" 2>&1; then
            _goh_rc=0
        else
            _goh_rc=$?
        fi
    fi
    if [ "$_goh_rc" -ne 0 ]; then
        printf '\n'
        # A TIMEOUT is named as one, before the tail. The tail of a step killed on a ceiling is
        # whatever it printed before it stalled, which is usually nothing at all -- and a red step
        # with no output and no reason reads exactly like the 2026-10-03 incident: a run that hangs
        # and says nothing. So the number, the reason, and the survivors come first.
        if [ "$_goh_rc" -eq 124 ]; then
            err "$label: TIMED OUT after ${_goh_limit}s (GOH_STEP_TIMEOUT; default ${_goh_step_default_timeout})"
            err "  Raise it in .gatesrc if the step genuinely needs longer. An unbounded step cannot"
            err "  be told apart from a hung one — and a hung test step used to be the silent failure."
        fi
        # The FAILURES first, then the tail: a 173-test `swift test` puts its
        # nine failing cases far above the last 60 lines, and the log is
        # deleted at exit — a red gate that names no test is a red gate
        # nobody can act on (zinc E.1c, 2026-09-21). GOH_FAIL_PATTERN is
        # the grep; GOH_FAIL_LINES caps how many hits are shown.
        # Only lines ATTRIBUTABLE to the sub-step that failed: a passing sibling's `✗` (a
        # calibration plant's quoted row) is left out and counted, and an unframed log says it is
        # one (ZoneWM H2, BACKLOG C5; lib/fail_lines.py).
        if python3 "$GOH_ROOT/lib/fail_lines.py" "$GOH_LOG" > "$GOH_LOG.fails" \
            && [ -s "$GOH_LOG.fails" ]; then
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

# goh_require <tool> <how to install>
# A gate whose tool is missing FAILS, before any slow step runs. It used to warn and carry on
# green ("cargo-machete not installed -- NOT checked", "swiftlint not installed -- lint gate
# skipped"): a step that did not run, under a passing result (tests/test_required_tools.py).
goh_require() {
    command -v "$1" >/dev/null 2>&1 \
        || die "$GOH_NAME: $1 is not installed, and this gate does not pass without the step it runs -- $2"
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
        # GOH_TIMINGS_CWD: the timing line names <dir>, not the caller's cwd (lib/step_timings.py).
        GOH_TIMINGS_CWD="$(cd "$dir" 2>/dev/null && pwd || printf %s "$dir")" \
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
#
# The export's root carries a `.git` FILE pointing at this repo's git dir, so
# git run INSIDE the view answers from the same index: `rev-parse
# --show-toplevel` is the view, `ls-files` lists index entries and nothing
# untracked, `show :path` agrees with the bytes on disk. A checker that finds
# its repo from its cwd (the ceiling and ratchet checkers do) is correct in
# the view unchanged. Two hook-environment facts make that hold:
#   - a plain `git commit` hands its hook GIT_INDEX_FILE=.git/index, RELATIVE,
#     which inside the view names a path under a FILE; it and GIT_DIR are
#     made absolute here (the hook's cwd is the one git meant them from);
#   - GIT_WORK_TREE outranks the `.git` file, so git in the view would read
#     the working tree again. That is refused with the reason, never obeyed.
goh_index_view() {
    local dir="$1" top phys rel snap gitdir
    GOH_INDEX_VIEW=""
    if [ -n "${GIT_WORK_TREE:-}" ]; then
        err "$GOH_NAME: GIT_WORK_TREE is set ($GIT_WORK_TREE) — it would override the index view,"
        err "  so --staged cannot judge the index. Unset it for the commit."
        exit 2
    fi
    case "${GIT_INDEX_FILE:-/}" in /*) ;; *) export GIT_INDEX_FILE="$PWD/$GIT_INDEX_FILE" ;; esac
    case "${GIT_DIR:-/}" in /*) ;; *) export GIT_DIR="$PWD/$GIT_DIR" ;; esac
    top="$(cd "$GOH_REPO_ROOT" && pwd -P)" || die "$GOH_NAME: cannot resolve $GOH_REPO_ROOT"
    phys="$(cd "$dir" && pwd -P)" || die "$GOH_NAME: cannot resolve $dir"
    case "$phys/" in
        "$top"/*) ;;
        *) return 1 ;;
    esac
    rel="${phys#"$top"}"
    rel="${rel#/}"
    gitdir="$(git -C "$top" rev-parse --absolute-git-dir)" && [ -n "$gitdir" ] \
        || die "$GOH_NAME: cannot resolve the git dir of $top"
    # Every step is checked by hand: callers use this in an `if`, where
    # `set -e` is off, and an empty $snap would make the prefix `/`.
    snap="$(mktemp -d "${TMPDIR:-/tmp}/goh-index.XXXXXX")" && [ -n "$snap" ] \
        || die "$GOH_NAME: cannot create a temp dir for the index export"
    GOH_TMPDIRS="${GOH_TMPDIRS:+$GOH_TMPDIRS }$snap"
    if ! git -C "$top" ls-files -z -- "${rel:-.}" \
            | git -C "$top" checkout-index -z --stdin --prefix="$snap/"; then
        die "$GOH_NAME: could not export the index under ${rel:-.} — refusing to check the working tree in its place"
    fi
    printf 'gitdir: %s\n' "$gitdir" > "$snap/.git" || die "$GOH_NAME: cannot write $snap/.git"
    GOH_INDEX_VIEW="$snap${rel:+/$rel}"
    # A subtree with nothing staged is an EMPTY corpus, not a missing one:
    # the checker's own floor then says so instead of a path error.
    mkdir -p "$GOH_INDEX_VIEW" || die "$GOH_NAME: cannot create $GOH_INDEX_VIEW"
}

# goh_done — the ONLY way a gate earns exit 0 (see the trap in goh_init).
goh_done() {
    goh_tree_check || die "$GOH_NAME: refusing a pass over a tree that moved"
    GOH_COMPLETED=1
    printf '\n'
    ok "all $GOH_NAME gates passed"
}
