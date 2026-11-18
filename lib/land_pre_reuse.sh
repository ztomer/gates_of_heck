# shellcheck shell=bash disable=SC2154,SC2034  # pre_reused, pre_fast_ran: land.sh's ledger fields; tip, here, main, pre: tools/land.sh's, which sources it
# lib/land_pre_reuse.sh -- a land's pre-step verdict, reused where the tip moved only in paths no
# test reads (GOH.PRELANDTWICE). Sourced by tools/land.sh, whose land_pre, tip, here, main, pre,
# verdicts and GOH_PY it reads; lib/pre_reuse.py decides. Pinned by tests/test_pre_reuse.py.
#
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# land_pre_from -- `goh land --enqueue` kept its green pre-step's tip and record
# (GOH_LAND_PRE_KEEP), and the lander names them (GOH_LAND_PRE_FROM): a tip that moved only where
# no test reads is not run again. A missing or unreadable keep is no verdict: the set runs. Both
# are read here and unset: no child of the pre-step or the gate sees them (a GOH_ key in the
# gate's environment would vary its proven-step key, gates/verdict_free_keys.txt).
land_pre_from() {
    pre_judged="" pre_from="${GOH_LAND_PRE_FROM:-}" pre_keep="${GOH_LAND_PRE_KEEP:-}"
    unset GOH_LAND_PRE_FROM GOH_LAND_PRE_KEEP
    if [ -n "$pre" ] && [ -z "${GOH_LAND_PRE_ONLY:-}" ] && [ -n "$pre_from" ]; then
        IFS= read -r pre_judged <"$pre_from" 2>/dev/null || pre_judged=""
        if ! [[ "$pre_judged" =~ ^[0-9a-f]{40}$ ]] || ! git cat-file -e "$pre_judged^{commit}" 2>/dev/null; then
            info "pre-land: no verdict from the enqueue ($pre_from unreadable or names no commit) -- run again"
            pre_judged=""
        elif [ -s "$pre_from.record" ]; then
            verdicts="$(mktemp "$(goh_tmp)/goh-land-verdicts.XXXXXX")" || exit 2
            cp "$pre_from.record" "$verdicts" || exit 2
        fi
        rm -f "$pre_from" "$pre_from.record"   # read once: one land's verdict
    fi
}
# land_pre_keep -- under GOH_LAND_PRE_ONLY, a green verdict kept for the lander (GOH_LAND_PRE_KEEP).
land_pre_keep() {
    if [ -n "$pre_keep" ] && [ -n "$pre_judged" ]; then   # green, with a verdict: kept for the lander
        if ! { { [ ! -s "$verdicts" ] || cp "$verdicts" "$pre_keep.record"; } \
                && printf '%s\n' "$pre_judged" >"$pre_keep.tmp" && mv -f "$pre_keep.tmp" "$pre_keep"; }; then
            warn "pre-land: the verdict could not be kept ($pre_keep) -- the lander runs the set again"
        fi
    fi
}
# land_pre_again WHOSE -- the pre-step on the tip, unless the verdict judged at pre_judged holds for
# it (GOH.PRELANDTWICE, lib/pre_reuse.py): the rebased tree differs from the judged one only in
# paths no test reads (tools/affected_tests.py `readers`, the selection's own notion). Moved paths
# some tests read run those tests alone; anything else, or no verdict to reuse, runs the whole set.
# The judged tip's record stays the gate's, which carries from it what the diff does not choose.
# A tree this land MOVED after a pre-step judged the lane (pre_judged: the enqueue's or this land's
# own; or a batch's stack, which the lander gates holding the lock, land_lock_ours) whose verdict does
# not hold runs the fast set alone when the gate measures (land_pre_fast, GOH.PRESTEPDUP).
land_pre_again() {
    local why rc=0 moved=() narrow cmd
    if [ -z "$pre_judged" ]; then
        { [ -n "$pre_fast" ] && land_lock_ours && land_pre_fast "on the batch's stack"; } || land_pre
        return
    fi
    why="$("$GOH_PY" "$GOH/lib/pre_reuse.py" --root "$here" --judged "$pre_judged" --tip "$tip" \
        --base "$(git -C "$main" rev-parse HEAD)" </dev/null 7>&- 8<&-)" || rc=$?
    why="${why%%$'\n'*}"   # the first line says why; tools/affected_tests.py chooses the nodes
    [ "$rc" -eq 0 ] || ! land_pre_fast "where the verdict $1 judged at ${pre_judged:0:8} does not hold" || return 0
    case $rc in
        0)  pre_reused="${pre_judged:0:12} whole"
            ok "pre-land: the verdict $1 judged at ${pre_judged:0:8} holds for ${tip:0:8} -- not run again: $why" ;;
        3)  info "pre-land: the verdict $1 judged at ${pre_judged:0:8} holds for ${tip:0:8} but what the moved paths' readers judge: $why"
            pre_reused="${pre_judged:0:12} readers"
            narrow="$(mktemp "$(goh_tmp)/goh-land-narrow.XXXXXX")" || exit 2
            mapfile -t moved < <(git diff --name-only --no-renames "$pre_judged" "$tip" 7>&-)
            printf -v cmd '%q ' "$GOH_PY" tools/affected_tests.py --run --readers-only --changed "${moved[@]}"
            land_pre "$cmd" "$narrow"
            rm -f "$narrow" "$narrow.judges" ;;
        *)  info "pre-land: the verdict $1 judged at ${pre_judged:0:8} is not ${tip:0:8}'s ($why) -- run again"
            land_pre ;;
    esac
}
# land_pre_fast WHERE -- GOH_LAND_PRE_FAST alone on the tip when lib/pre_fast.py says the gate will
# MEASURE coverage: its instrumented suite re-runs every test the whole set would, and what the set
# could fail sooner is already judged -- the lane's own commits by the set that judged them, the
# merged tree by the gate right after (a batch's or a rebase under the lock: no wait between them;
# BASETEMPSLOT's batch ran 353 s of set, then 665 s of suite over the same tests). 1, running nothing, when the gate may reuse the set's
# verdict (GOH.SUITEONCE) or none is declared: the whole set is owed. The fast record never stands
# for a whole selection (no gate reuses it); the judged record, kept for the gate's carry, stays.
# A red batch still bisects on gates: each half is a stack of its own, judged by its own gate.
land_pre_fast() {
    local why rc=0 rec
    [ -n "$pre_fast" ] && [ -z "${GOH_LAND_PRE_ONLY:-}" ] || return 1
    why="$("$GOH_PY" "$GOH/lib/pre_fast.py" --root "$here" --base "$(git -C "$main" rev-parse HEAD)" \
        ${cov_ext:+--external "$cov_ext"} </dev/null 7>&- 8<&-)" || rc=$?
    why="${why%%$'\n'*}"
    if [ "$rc" -ne 0 ]; then
        info "pre-land: the whole set ${1}: ${why:-lib/pre_fast.py exit $rc}"
        return 1
    fi
    info "pre-land: the fast set alone on ${tip:0:8} ${1}: $why"
    pre_fast_ran=1
    rec="$(mktemp "$(goh_tmp)/goh-land-fast.XXXXXX")" || exit 2
    land_pre "$pre_fast" "$rec"
    if [ -s "$rec" ] && [ -n "$verdicts" ] && [ ! -s "$verdicts" ]; then mv -f "$rec" "$verdicts"; fi
    rm -f "$rec" "$rec.judges"
}
