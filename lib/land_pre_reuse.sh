# shellcheck shell=bash disable=SC2154,SC2034  # pre_reused, pre_why: land.sh's ledger fields; tip, here, main, pre: tools/land.sh's, which sources it
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
land_pre_again() {
    local why out rc=0 moved=() also=() narrow cmd structural=()
    if [ -z "$pre_judged" ]; then
        # Every decision is recorded (GOH.SUITEONCEMISS); never worded "no verdict", which names a
        # pre-land past its deadline (GOH.PRESTEPDEADLINE).
        pre_why="rerun: nothing kept from the enqueue"
        [ -n "${pre_from:-}" ] || [ -n "${GOH_LAND_PRE_ONLY:-}" ] \
            || info "pre-land: nothing kept from the enqueue for this land -- the set runs"
        land_pre
        return
    fi
    # The judged verdict's record names the set it judged: a move to the pre-step's own code is
    # held to it (GOH.PRESTEPSELFMOVE, lib/pre_reuse.py `within`).
    out="$("$GOH_PY" "$GOH/lib/pre_reuse.py" --root "$here" --judged "$pre_judged" --tip "$tip" \
        --base "$(git -C "$main" rev-parse HEAD)" ${verdicts:+--record "$verdicts"} </dev/null 7>&- 8<&-)" || rc=$?
    why="${out%%$'\n'*}"   # the first line says why; tools/affected_tests.py chooses the nodes
    # The binary's build inputs moved (GOH.PRECODERUST): structural --full runs the binary, so the
    # narrow run does too, before the tests that run it; the verdict's other tests stand.
    [[ $'\n'"$out"$'\n' != *$'\n--structural\n'* ]] || structural=(--structural)
    # The tests the tip's set adds after a move to the pre-step's own code (GOH.SUITEONCEMISS): run
    # too, each a changed test file to the narrow run (`readers` chooses a changed test file).
    mapfile -t also < <(sed -n 's/^--also //p' <<<"$out")
    case $rc in
        0)  pre_reused="${pre_judged:0:12} whole" pre_why="reuse: $why"
            ok "pre-land: the verdict $1 judged at ${pre_judged:0:8} holds for ${tip:0:8} -- not run again: $why" ;;
        3)  info "pre-land: the verdict $1 judged at ${pre_judged:0:8} holds for ${tip:0:8} but what the moved paths' readers judge: $why"
            pre_reused="${pre_judged:0:12} readers" pre_why="narrow: $why"
            narrow="$(mktemp "$(goh_tmp)/goh-land-narrow.XXXXXX")" || exit 2
            mapfile -t moved < <(git diff --name-only --no-renames "$pre_judged" "$tip" 7>&-)
            moved+=(${also[@]+"${also[@]}"})
            printf -v cmd '%q ' "$GOH_PY" tools/affected_tests.py --run --readers-only ${structural[@]+"${structural[@]}"} --changed "${moved[@]}"
            land_pre "$cmd" "$narrow"
            rm -f "$narrow" "$narrow.judges" ;;
        *)  info "pre-land: the verdict $1 judged at ${pre_judged:0:8} is not ${tip:0:8}'s ($why) -- run again"
            pre_why="rerun: $why"
            land_pre ;;
    esac
}
