# shellcheck shell=bash
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# _proven_closure.sh — a proven step's goh closure line, sourced by gates/_proven.sh (its callers
# provide proven_dir, proven_tree, _proven_goh_roots, _proven_memo_key, GOH_PY, PROVEN_GOH_ROOT).
_PROVEN_CLOSURE_PY="$PROVEN_GOH_ROOT/lib/step_closure.py"

# THE STEP'S CLOSURE (GOH.PROVENCLOSURE). The goh half of a step's key is the content of the goh
# files that can decide THAT step (lib/step_closure.py: the runner's, and what the step
# string reaches through sources, imports, runs and names), not every file of every gates root: a
# ship that touched one scanner made every consumer's next push a cold full gate. The line rides
# in the key's step part (`step <step>\n<line>`, both faces, so the native key needs no change); the
# record keeps the bare step. A step the reader cannot place keys on the whole content (`closure
# full`). Only the step runners (local_ci.sh, proven.sh) set PROVEN_BY_CLOSURE=1, unexported: any
# other key (rust_gate's groups) is a goh label whose code the label does not name, so `full`.
# proven_closures_prime <step>... computes every step's line in ONE call, kept in
# GOH_PROVEN_CLOSURES for this memo key; a step it does not hold asks on its own.
_proven_closures_args() {
    local d
    _pc_args=()
    while IFS= read -r d; do
        [ -n "$d" ] && _pc_args+=(--root "$d")
    done < <(_proven_goh_roots)
    _pc_args+=(--repo "$(git rev-parse --show-toplevel 2>/dev/null || printf '%s' "$PWD")")
    [ -z "${PYTHONPATH+set}" ] || _pc_args+=(--pythonpath "$PYTHONPATH")
    local dir tree
    if dir="$(proven_dir)" && tree="$(proven_tree)" && [ -n "$tree" ]; then
        _pc_args+=(--cache "$dir-closure" --tree "$tree")
    fi
}

proven_closures_prime() {
    [ "${PROVEN_ON:-1}" = 1 ] && _proven_closure_on || return 0
    local s _pc_args
    _proven_closures_args </dev/null
    for s in "$@"; do _pc_args+=(--step "$s"); done
    GOH_PROVEN_CLOSURES="$("$GOH_PY" -S "$_PROVEN_CLOSURE_PY" "${_pc_args[@]}" </dev/null 2>/dev/null)" \
        || GOH_PROVEN_CLOSURES=""
    GOH_PROVEN_CLOSURES_FOR="$(_proven_memo_key)"
    export GOH_PROVEN_CLOSURES GOH_PROVEN_CLOSURES_FOR
}

# THE SWITCH (GOH.PROVENCLOSURE2). GOH_PROVEN_CLOSURE=on keys as above; off (the default, until
# GOH.REUSEAUDIT samples `step`) keys every step on the whole goh content as before GOH.PROVENCLOSURE:
# the bare step, the `goh` content lines in the identity (lib/gate_identity.py proven), and no
# closure computed at all. proven_settings refuses any other value (exit 2).
_proven_closure_on() { [ "${GOH_PROVEN_CLOSURE:-off}" = on ]; }
_proven_closure_valid() {
    case "${GOH_PROVEN_CLOSURE:-off}" in
        off|on) return 0 ;;
    esac
    printf 'GOH_PROVEN_CLOSURE must be off or on (got %s)\n' "'${GOH_PROVEN_CLOSURE}'" >&2
    return 2
}

# _proven_key_step <step> — the step part of the key: the step, then its closure line when on.
_proven_key_step() {
    if _proven_closure_on; then
        printf '%s\n%s\n' "$1" "$(_proven_closure_line "$1")"
    else
        printf '%s\n' "$1"
    fi
}

# _proven_closure_line <step> — the step's goh line: from the prime, else one call of its own.
_proven_closure_line() {
    local line want="full" got="" _pc_args
    [ "${PROVEN_BY_CLOSURE:-0}" = 1 ] && want="$1"
    if [ "${GOH_PROVEN_CLOSURES_FOR:-}" = "$(_proven_memo_key)" ]; then
        while IFS= read -r line; do
            [ "${line#*$'\t'}" = "$want" ] || continue
            got="${line%%$'\t'*}"
            break
        done <<<"${GOH_PROVEN_CLOSURES:-}"
    fi
    if [ -z "$got" ]; then
        _proven_closures_args
        [ "$want" = full ] || _pc_args+=(--step "$want")
        got="$("$GOH_PY" -S "$_PROVEN_CLOSURE_PY" "${_pc_args[@]}" </dev/null 2>/dev/null | tail -n 1)"
        got="${got%%$'\t'*}"
    fi
    printf '%s\n' "${got:-closure unreadable $$}"
}
