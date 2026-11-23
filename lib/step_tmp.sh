# shellcheck shell=bash
# Sourced by gates/local_ci.sh. Every step runs with a TMPDIR of its own, and what a GREEN step
# leaves in it is named and removed (EST.TMPLEAK).
#
# THE CASE (EST.FSEVENTSCHURN, 2026-11-14). The user's $TMPDIR held 34,022 top-level entries
# (2.6 GiB), 23,535 older than a day: test and tool scratch nobody removed (`TemporaryDirectory.*`,
# `tmpXXXXXXXX`, `<tool>-test.*`, a long tail across the estate). fseventsd spent 7.4k of ~12k
# path checks in 10 s inside $TMPDIR. A leak was invisible: nothing ever looked at what a passing
# suite left behind. Now:
#
#   step_tmp_prepare            once per run: bound the slots killed or red runs kept, write the
#                               sccache shim (below) into "$LOGDIR/bin"
#   step_tmp_claim <i>          before step i runs: STEP_TMP[i], the lowest free
#                               `$(goh_tmp)/step.NN` -- a SLOT, not a fresh name, so every
#                               path a step writes is the path the last run wrote (GOH.FSCHURN:
#                               fresh names are what fseventsd pays for)
#                               and SHORT: a socket path is 104 bytes on macOS, and the
#                               slot adds only `goh.noindex/step.NN/` (20) to a test's
#   step_tmp_env <i>            inside step i's runner: TMPDIR is that slot
#   step_tmp_judge <label> <i> <rc>  after step i: green -> every entry left is a LEAK, named, then the
#                               slot is removed; red -> the slot is KEPT, like the step's log (a red
#                               suite keeps its temp trees on purpose: they explain the failure)
#   step_tmp_stale              at a green run's end: an allow pattern nothing matched is stale
#
# A leak is a WARNING by default; a repo whose suites are clean says GOH_TMP_LEAKS=red in its
# .gatesrc and every new leak fails the step. GOH_TMP_LEAK_ALLOW='glob ...' names the entries a
# red repo still leaks (each a ratchet entry: in a run where every step ran, one that matched
# nothing fails, so the list only shrinks). GOH_TMP_LEAKS=off runs steps in the user's TMPDIR.
#
# A SHARED DAEMON MUST NOT INHERIT THE SLOT (GOH.SCCACHETMP, tests/suite_tmp.py). The first compile
# with no sccache server running starts the machine's one server, which inherits TMPDIR and outlives
# the step; inside a removed slot every cargo build on the machine fails "Failed to create temp
# dir". So PATH leads with an `sccache` that runs the real one under the user's TMPDIR -- the same
# shim, and the same mark, as the suite's, so a goh suite inside a step does not wrap it twice.
# Pinned by tests/test_step_tmp.py.
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/step_tmp.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/tmp_root.sh
. "${BASH_SOURCE[0]%/*}/../lib/tmp_root.sh"   # goh_tmp
# shellcheck source=lib/rm_tree.sh
. "${BASH_SOURCE[0]%/*}/../lib/rm_tree.sh"    # goh_rm_tree

STEP_TMP_SHIM_MARK="# goh-suite-sccache-shim"   # tests/suite_tmp.py SHIM_MARK: one shim, one mark

step_tmp_mode() {
    case "${GOH_TMP_LEAKS:-warn}" in
        warn|red|off) printf '%s\n' "${GOH_TMP_LEAKS:-warn}" ;;
        *) printf '%s\n' "✗ GOH_TMP_LEAKS must be warn, red or off (got '${GOH_TMP_LEAKS}')" >&2; return 2 ;;
    esac
}

_step_tmp_q() { printf "'%s'" "${1//\'/\'\\\'\'}"; }   # one shell word, single-quoted

step_tmp_prepare() {
    STEP_TMP_MODE="$(step_tmp_mode)" || return 2
    STEP_TMP_USER="${TMPDIR:-/tmp}"
    [ "$STEP_TMP_MODE" != off ] || return 0
    local tmp real
    tmp="$(goh_tmp)" || return 1
    "$GOH_PY" -S "$GOH_ROOT/lib/prune_kept.py" "$tmp" step. 3 3600 --max-bytes 1073741824 2>/dev/null || true
    real="$(command -v sccache 2>/dev/null)" || real=""
    if [ -n "$real" ] && ! grep -q "$STEP_TMP_SHIM_MARK" "$real" 2>/dev/null; then
        mkdir -p "$LOGDIR/bin" || return 1
        printf '#!/bin/sh\n%s\nTMPDIR=%s HOME=%s\nexport TMPDIR HOME\nexec %s "$@"\n' \
            "$STEP_TMP_SHIM_MARK" "$(_step_tmp_q "$STEP_TMP_USER")" "$(_step_tmp_q "$HOME")" "$(_step_tmp_q "$real")" >"$LOGDIR/bin/sccache"
        chmod 755 "$LOGDIR/bin/sccache"
        STEP_TMP_PATH="$LOGDIR/bin"
    fi
    : >"$LOGDIR/tmp-allow-used"
}

step_tmp_claim() {
    local i="$1" tmp n=0 d
    STEP_TMP[i]=""
    [ "$STEP_TMP_MODE" != off ] || return 0
    tmp="$(goh_tmp)" || return 1
    while [ "$n" -lt 32 ]; do
        d="$tmp/step.$(printf %02d "$n")"
        if mkdir "$d" 2>/dev/null; then STEP_TMP[i]="$d"; break; fi
        n=$((n + 1))
    done
    [ -n "${STEP_TMP[i]}" ] || STEP_TMP[i]="$(mktemp -d "$tmp/step.XXXXXX")" || return 1
    # `<pid> <start time>`: lib/prune_kept.py never cuts the slot of a run that is still alive
    printf '%s %s\n' "$$" "$(ps -o lstart= -p "$$" 2>/dev/null)" >"${STEP_TMP[i]}/.goh-owner"
}

step_tmp_env() {
    [ -n "${STEP_TMP[$1]:-}" ] || return 0
    export TMPDIR="${STEP_TMP[$1]}"
    [ -z "${STEP_TMP_PATH:-}" ] || export PATH="$STEP_TMP_PATH:$PATH"
}

# _step_tmp_left <slot> -- one line per entry a step left: top-level names, and inside the one
# `goh.noindex` parent goh's own roots live under (its empty self is the machine's, not a leak).
_step_tmp_left() {
    local d="$1" e
    for e in "$d"/* "$d"/.[!.]* "$d"/..?*; do
        { [ -e "$e" ] || [ -L "$e" ]; } || continue
        case "${e##*/}" in
            .goh-owner) ;;
            goh.noindex) _step_tmp_left "$e" | sed 's|^|goh.noindex/|' ;;
            *) printf '%s\n' "${e##*/}" ;;
        esac
    done
}

step_tmp_judge() {
    local label="$1" rc="$3" d="${STEP_TMP[$2]:-}" name pat known new=0 shown="" pats=()
    read -r -a pats <<<"${GOH_TMP_LEAK_ALLOW:-}"   # split, never globbed against the cwd
    [ -n "$d" ] && [ -d "$d" ] || return 0
    if [ "$rc" -ne 0 ]; then
        step "its temp dir is kept with the log: $d"
        return 0
    fi
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        known=""
        for pat in "${pats[@]}"; do
            # shellcheck disable=SC2254  # a glob from .gatesrc is the feature
            case "$name" in $pat) known="$pat"; printf '%s\n' "$pat" >>"$LOGDIR/tmp-allow-used"; break ;; esac
        done
        [ -z "$known" ] || continue
        new=$((new + 1))
        [ "$new" -gt "${GOH_WARN_LINES:-20}" ] || shown="$shown $name"
    done <<<"$(_step_tmp_left "$d")"
    goh_rm_tree "$d"
    [ "$new" -gt 0 ] || return 0
    if [ "$STEP_TMP_MODE" = red ]; then
        err "$label PASSED, and left $new temp $( [ "$new" -eq 1 ] && echo entry || echo entries) (removed):$shown"
    else
        warn "$label left $new temp $( [ "$new" -eq 1 ] && echo entry || echo entries) (removed):$shown"
    fi
    warn "  a test must remove what it makes in TMPDIR (a context manager, a trap); GOH_TMP_LEAK_ALLOW lists a known one"
    [ "$STEP_TMP_MODE" != red ] || return 1
}

# step_tmp_stale <every-step-ran> -- 1 when, in a red repo's run where every step ran, an allow
# pattern matched nothing: the ratchet entry is stale and goes.
step_tmp_stale() {
    local pat bad=0 pats=()
    [ "$STEP_TMP_MODE" = red ] && [ "$1" = 1 ] || return 0
    read -r -a pats <<<"${GOH_TMP_LEAK_ALLOW:-}"
    for pat in "${pats[@]}"; do
        grep -qxF -- "$pat" "$LOGDIR/tmp-allow-used" 2>/dev/null && continue
        err "GOH_TMP_LEAK_ALLOW: '$pat' matched no step's temp entry -- the leak is fixed; delete the entry"
        bad=1
    done
    return "$bad"
}
