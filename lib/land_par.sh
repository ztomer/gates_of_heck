# shellcheck shell=bash disable=SC2154,SC2034  # pre, verdicts, base, tip, gate, judge_*, tip_*, pre_*: tools/land.sh's, which sources this
# lib/land_par.sh -- a land's pre-land tests and its gate AT ONCE (GOH.GATEPARALLEL), sourced by
# tools/land.sh under the land lock, after the rebase main will fast-forward to.
#
# WHY. Both judge the same rebased tree and neither reads the other's output, yet the lander ran them
# one after the other: pre-land 255 s then the gate 393 s, medians over 94 plain lands
# (`goh land-profile`, 2026-11-21), so a land cost their sum. Now each runs in a branch of its own
# and the land costs the longer one:
#   * nothing lands unless both are green on this tree (a pre-land past its deadline is no verdict,
#     never red, as before: GOH.PRESTEPDEADLINE);
#   * the FIRST red stops the other branch whole (lib/pid_owner.sh goh_pid_tree_kill: its subshell,
#     its load.py, the step) and is the one named; its exit is the land's (1 red, 75 the gate never
#     got a slot, which the lander reads as no verdict);
#   * each branch keeps its own deadline (the pre-step's share deadline, lib/pre_deadline.py; the
#     gate's GOH_LAND_GATE_MAX) and its own admission, as when they ran in turn;
#   * the pre-land records into a file of its own: the gate's suite reads the enqueue's kept record
#     ($verdicts, carried for what the diff does not choose: GOH.VERDICTCARRY), never a file the
#     branch beside it is still writing. The pre-land's ledger fields come back through a file.
# ONE PYTEST PER LANDER RUN (GOH.LANDONERUN): where the gate's suite reuses the enqueue's kept record,
# no pre-land runs beside it (land_par, below); the gate's one run fails fast in its place.
# Pinned by tests/test_land_par.py (both at once, each red first, each hanging to its deadline, one run).
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
#
# `land_par`, a land's pre-land tests and its gate AT ONCE under the land lock (GOH.GATEPARALLEL):
# each a branch with its own admission and deadline, the first red stops the other whole
# (`goh_pid_tree_kill`) and is named, the pre-land records into its own file and hands its ledger
# fields back. Pinned by `test_land_par.py`.
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/land_par.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/pid_owner.sh
. "$_goh_d/pid_owner.sh" || exit 2

# land_par -- the pre-land (when the repo declares one) and the gate(s), at once; exits on the first red.
land_par() {
    local dir name rc other left=0 i
    local -A stamp=() pid=()
    dir="$(mktemp -d "$(goh_tmp)/goh-land-par.XXXXXX")" || exit 2
    [ -n "$pre_start" ] || pre_start="$(mktemp "$(goh_tmp)/goh-land-prestart.XXXXXX")" || exit 2
    mkfifo "$dir/done" || exit 2
    exec 9<>"$dir/done"
    # ONE PYTEST PER LANDER RUN (GOH.LANDONERUN): the gate's suite that reuses the enqueue's kept
    # record ($verdicts, tools/pytest.sh) runs every test the pre-land would run on this tree, or
    # more (the diff's readers, and the whole suite where nothing carries), judges first and
    # stopping at the first red. The pre-land beside it ran that set a second time: 1,300-3,800
    # cpu-s a land (docs: testonce_design 2.2d). Its REUSE/NARROW/RERUN is still decided and logged
    # (pre_why); nothing of it runs. With no kept record the gate cannot carry, and the pre-land
    # stays beside it as before.
    if [ -n "$pre" ] && [ -s "${verdicts:-}" ]; then
        land_pre_again "the enqueue" decide
    elif [ -n "$pre" ]; then
        ( set +e
          ( trap 'printf "%s\n" "$pre_wall" "$pre_run" "$pre_workers" "$pre_reused" "$pre_why" "$pre_cpu_s" >"$dir/pre.vars"' EXIT
            set -e; pre_record="$dir/pre.record"
            land_pre_again "the enqueue" ) 9>&-
          echo "pre-land $?" >&9 ) 7>&- 8<&- </dev/null &
        pid[pre-land]=$!
        stamp[pre-land]="$(goh_pid_stamp "$!")" || stamp[pre-land]=""
        left=$((left + 1))
    fi
    ( set +e
      ( set -e; [ -z "$judge_root" ] || land_gate "$judge_root" "main ${base:0:8}" "$judge_gate"
        land_gate "$tip_root" "$tip_who" "$gate" ) 9>&-
      echo "gate $?" >&9 ) 7>&- 8<&- </dev/null &
    pid[gate]=$!
    stamp[gate]="$(goh_pid_stamp "$!")" || stamp[gate]=""
    left=$((left + 1))
    while [ "$left" -gt 0 ]; do
        read -r name rc <&9
        left=$((left - 1))
        wait "${pid[$name]}" 2>/dev/null || true
        unset "pid[$name]" "stamp[$name]"
        [ "$rc" -eq 0 ] && continue
        for other in "${!pid[@]}"; do
            goh_pid_tree_kill TERM "${stamp[$other]:-}" || true
            for ((i = 0; i < 50; i++)); do goh_pid_alive "${stamp[$other]:-}" || break; sleep 0.1; done
            goh_pid_tree_kill KILL "${stamp[$other]:-}" || true
            wait "${pid[$other]}" 2>/dev/null || true
            err "land.sh: the $name went red first -- the $other was stopped -- nothing landed"
        done
        [ -n "${pid[*]}" ] || err "land.sh: the $name went red -- nothing landed"
        land_par_end "$dir"
        exit "$rc"
    done
    land_par_end "$dir"
}

# land_par_end DIR -- the pre-land's ledger fields into this shell, its record and the fifo gone.
land_par_end() {
    exec 9>&-
    # Read, never sourced: one value a line, as the pre-land branch above wrote them.
    [ ! -s "$1/pre.vars" ] || { IFS= read -r pre_wall; IFS= read -r pre_run; IFS= read -r pre_workers
                                IFS= read -r pre_reused; IFS= read -r pre_why; IFS= read -r pre_cpu_s; } <"$1/pre.vars" || true
    rm -rf "$1"
}
