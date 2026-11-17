#!/usr/bin/env bash
# land.sh -- from a worktree: gate the branch tip, then fast-forward the main checkout to THAT sha.
#
# The house workflow lands a branch only after its own push gate is green on the tip
# (the kit's `tools/gate_profile.sh .`, by path). Done by hand it was `gate; merge`, and on 2026-10-08 a `;` where
# `&&` belonged moved main onto a red gate run (engineering rule #3: gates are structural, not
# disciplinary). So the two are one command:
#
#   0. the repo's declared pre-step (GOH_LAND_PRE; goh's runs the tests its diff chooses,
#      tools/affected_tests.py) runs before the land lock and the gate's slot (THE PRE-STEP, below);
#   1. the tip is read once, and the gate runs: GOH_LAND_GATE from the environment, else the one the
#      repo DECLARES in its .gatesrc (read by name in a child, like GOH_RELEASE_GATE), else the kit's
#      gate_profile.sh over the repo (its push gate, which reads that same .gatesrc). The default
#      was the relative `tools/gate_profile.sh .`, which only gates_of_heck has: in ztools it did
#      not exist and an agent had to pass GOH_LAND_GATE by hand (GOH.CUTTITLE);
#   2. a red gate lands nothing; a tip that moved while it was gated lands nothing (the gate
#      judged a different commit);
#   3. the main checkout (the first `git worktree list` entry) merges the GATED SHA with
#      `--ff-only` -- never the branch name, which may have moved -- and a main that moved on
#      outside land.sh during the gate is named: land again.
#
# LANDS QUEUE (GOH.LANDSERIAL). With four lanes landing into one main, a ~14 min green gate was
# thrown away whenever main moved under it (the hooks lane spent its 3-attempt budget that way,
# 2026-11-14). So one land at a time holds the repository's land lock, `goh-land.lock` in the common
# git dir, from the rebase through the fast-forward: a waiter prints who holds it, then rebases the
# branch onto the main the holder landed (a conflict aborts the rebase and lands nothing) and gates
# THAT commit, the one main fast-forwards to. The lock is tree_lock.sh's idiom, a kernel flock on a
# shell fd, released on any exit; the gate and the merge run with that fd closed, so a daemon a gate
# starts never holds later lands off. Pinned by tests/test_land_serial.py. goh ship holds the same lock
# for its pipeline (lib/land_lock.py); a land its release kit runs proceeds under that hold.
#
# LOCK ORDER (GOH.LOCKORDER, lib/lock_order.tsv). The land lock comes BEFORE the gate's admission:
# this script takes the land lock, then admits the gate itself (`tools/load.py 0 --`, a gate slot).
# It is run bare, `bash tools/land.sh`; run inside a holder (`load.py 0 -- bash tools/land.sh`) it is
# refused at once, exit 78: queued on the land lock there, it held the slot a ship holding the land
# lock was waiting for, and v0.122.0's release waited 1800 s on 2026-11-15
# (tests/test_lock_order_deadlock.py).
#
# The gate proves WITH the proven-step cache on (G2f, GOH_PROFILE_PROVEN): gate_profile.sh keeps it
# off by default because a profile of cache hits measures the cache, but landing wants the verdict,
# and the push that follows the fast-forward is the same tree -- it reuses what this gate recorded
# instead of re-running 168 s of it (tests/test_proven.py, both directions). A caller that set
# GOH_PROFILE_PROVEN keeps its value.
#
# What it claims it verifies (G15): the tip must differ from main's, the tree must be clean, main must
# equal the gated tip afterwards, and GOH_LAND_EXPECT="path ..." names paths the landed range must change;
# anything else is a non-zero exit naming what did not happen.
#
# Nothing is pushed. The gated SHA is recorded in the common git dir (`goh-landed`), which a repo
# that sets GOH_LANDED_ONLY=<branch> has its push read: a tip that did not come through here is
# refused (BACKLOG 1.5). Pinned by tests/test_land.py.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=lib/timing_ledger.sh
. "$GOH/lib/timing_ledger.sh"

# ONE EXIT: the pre-step's scratch files go, and the land's ledger row is written (GOH.GATEBUDGET,
# lib/budget.py KINDS `land`: from the land lock taken to main fast-forwarded or the land refused;
# the wait for the lock is its own field, not gate time). A land that never took the lock (a
# refusal before it, a failed pre-step) measured no gate and writes no row. The ledger never fails
# a land: the script's exit status is what it was.
verdicts="" pre_start="" land_timings="" lock_wait=0 pre_wall=""
# shellcheck disable=SC2329  # invoked by the EXIT trap below
land_exit() {
    local rc=$?
    rm -f "$verdicts" "$pre_start"
    [ ! -s "${land_timings:-}" ] && rm -f "${land_timings:-}" "${land_timings:-}.log" "${land_timings:-}.controls.log"
    goh_span_record land "$(goh_repo_id "${main:-.}")" "$([ "$rc" -eq 0 ] && echo 1 || echo 0)" \
        ${land_timings:+--timings "${GOH_TIMINGS:-$land_timings}"} --field "wait_lock_s=$lock_wait" \
        --field "exit=$rc" ${pre_wall:+--field "pre_s=$pre_wall"} --field "sha=$(printf %.12s "${tip:-}")" || true
}
trap land_exit EXIT

here="$(git rev-parse --show-toplevel)"
main="$(git worktree list --porcelain | { read -r _ path; printf '%s' "$path"; })"
if [ "$(cd "$here" && pwd -P)" = "$(cd "$main" && pwd -P)" ]; then
    err "land.sh: run it from the branch's worktree, not the main checkout ($main)"
    exit 2
fi
tip="$(git rev-parse HEAD)"
branch="$(git rev-parse --abbrev-ref HEAD)"
gate="${GOH_LAND_GATE:-}"
if [ -z "$gate" ] && [ -f "$here/.gatesrc" ]; then
    gate="$(cd "$here" && bash -c 'unset GOH_LAND_GATE; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_GATE-}"')" \
        || { err "land.sh: cannot read $here/.gatesrc"; exit 2; }
fi
# The verdict's MODE (GOH.VERDICTKEY): `full` for the kit's own push gate, else the gate string --
# a hand-set GOH_LAND_GATE=true lands, but its goh-landed line is no full gate's (lib/gate_identity.py).
mode="${gate:-full}"
[ -n "$gate" ] || gate="$GOH/tools/gate_profile.sh ."

section "land $branch @ ${tip:0:8} -> $main"
if [ -n "$(GIT_OPTIONAL_LOCKS=0 git status --porcelain --untracked-files=no)" ]; then
    err "land.sh: uncommitted changes to tracked files in $here -- the gate would judge a tree that is not ${tip:0:8} -- nothing landed"
    exit 1
fi
# THE PRE-STEP (GOH.TREETESTSFIRST): the tests the diff chooses, BEFORE the land lock and the gate's
# slot, so a test the lane never ran fails in seconds, not after a lock wait, a slot wait and ~8 min
# of suite (GOH.ONEADMISSION's first land, 2026-11-16, lost to tests/test_lock_order.py). Declared in
# .gatesrc as GOH_LAND_PRE (goh's: tools/affected_tests.py --run, the changed paths' tests and every
# tree reader); run with main's tip as $1, in the worktree, admitted in the short lane (a 0-spinner
# load.py run takes a gate slot, lib/admission.py; GOH.PRESTEPADMIT below) and released before the lock is queued for, so
# it nests no lock (GOH.LOCKORDER). Bounded at 10 min; a repo that declares none has no pre-step.
pre="${GOH_LAND_PRE-}"
if [ -z "${GOH_LAND_PRE+set}" ] && [ -f "$here/.gatesrc" ]; then
    pre="$(cd "$here" && bash -c 'unset GOH_LAND_PRE; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_PRE-}"')" \
        || { err "land.sh: cannot read $here/.gatesrc"; exit 2; }
fi
# ONE RUN PER TEST (GOH.LANDPREDUP): a goh suite run by the pre-step records the tests it passed
# (GOH_LAND_VERDICTS_RECORD) and the gate's suite skips them on the same tree and tools
# (GOH_LAND_VERDICTS_REUSE, tests/_land_verdicts.py), the instrumented coverage run keeping every
# test that ran the binary. One file per land, removed with it. A rebased tip CARRIES verdicts from
# the pre-step's tree and main's recorded tip, for what the diff between them does not choose
# (GOH.VERDICTCARRY, the one store's `carry` path).
if [ -n "$pre" ]; then
    verdicts="$(mktemp "${TMPDIR:-/tmp}/goh-land-verdicts.XXXXXX")" || exit 2
    rm -f "$verdicts"   # the pre-step's suite writes it whole, or there is no record
    pre_base="$(git -C "$main" rev-parse HEAD)"
    info "pre-land: $pre"
    # The SHORT lane (GOH.PRESTEPADMIT): the commit gate's slots, which no long gate takes, so the
    # pre-step starts while long gates hold every long slot -- a land queues for a long slot ONCE,
    # for its gate. As a `ship` gate it queued twice, and a lane that would go red waited up to
    # 632 s first (2026-11-16). The wait is printed apart from the run: the step stamps its start.
    pre_start="$(mktemp "${TMPDIR:-/tmp}/goh-land-prestart.XXXXXX")" || exit 2
    pre_max="${GOH_LAND_PRE_MAX:-10m}"   # the deadline; a seam so a test can reach it
    pre_t0=$(date +%s)
    pre_rc=0
    (cd "$here" && GOH_LAND_VERDICTS_RECORD="$verdicts" GOH_GATE_CLASS=short "$GOH_PY" "$GOH/tools/load.py" \
        --background-ok "pre-land" --max "$pre_max" 0 -- \
        bash -c 'date +%s >"$1"; shift; exec bash -c "$@"' pre-mark "$pre_start" "$pre" pre-land "$pre_base" \
        </dev/null) || pre_rc=$?
    pre_t1=$(date +%s)
    pre_s=""
    IFS= read -r pre_s <"$pre_start" 2>/dev/null || true
    [[ "$pre_s" =~ ^[0-9]+$ ]] || pre_s=$pre_t1   # never started: all of it was the wait
    pre_wall=$((pre_t1 - pre_t0))
    pre_took="in $((pre_t1 - pre_t0)) s: waited $((pre_s - pre_t0)) s, ran $((pre_t1 - pre_s)) s"
    # A pre-step that reached its deadline (load.py's EXIT_DEADLINE, 124) found nothing: it exists
    # to fail FAST, and the gate runs the whole suite anyway -- no verdict, never red
    # (GOH.PRESTEPDEADLINE: GOH.REUSEAUDIT's land, killed at 82% of a green-so-far run).
    if [ "$pre_rc" -eq 124 ]; then
        load1="$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')"
        [ -n "$load1" ] || load1="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
        info "pre-land: no verdict after $((pre_t1 - pre_s)) s at load ${load1:-?} -- the gate decides ($pre_took)"
    elif [ "$pre_rc" -ne 0 ]; then
        err "land.sh: the pre-land tests failed on ${tip:0:8} $pre_took, before the land lock and the gate -- nothing landed"
        exit 1
    else
        ok "pre-land green $pre_took"
    fi
fi
# The land lock (GOH.LANDSERIAL, header): fd 7, held until this script exits.
# shellcheck source=lib/lock_order.sh
. "$GOH/lib/lock_order.sh" || exit 1
lock="$(git -C "$main" rev-parse --path-format=absolute --git-common-dir)/goh-land.lock"
exec 7>>"$lock"
lock_t0="${EPOCHREALTIME/,/.}"
holder=""
IFS= read -r holder <"$lock" 2>/dev/null || true
# A land run BY the lock's holder (goh ship's release kit, lib/land_lock.py) is that holder's own
# land: GOH_LAND_LOCK_OWNER names this lock and a live pid, and the file names that same pid.
owner="${GOH_LAND_LOCK_OWNER:-}"
owner_pid="${owner##* }"
if [ "${owner% *}" = "$lock" ] && [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null \
    && [[ "$holder" == *"(pid $owner_pid,"* ]]; then
    info "land lock held for this land by its caller: $holder"
else
    # The order first (GOH.LOCKORDER): a land inside a holder of a later lock -- the slot of
    # `load.py 0 -- land.sh`, the deadlock of 2026-11-15 -- is refused before it can queue.
    if ! goh_lock_check land "$lock"; then
        err "land.sh: run it bare, \`bash tools/land.sh\`: it takes the land lock first and the gate's slot after -- nothing landed"
        exit 78
    fi
    if ! goh_lock_take land 7 --nb --where "$lock"; then
        info "waiting for the land lock: held by ${holder:-another land} ($lock)"
        goh_lock_take land 7 --where "$lock" || exit
        ok "land lock acquired"
    fi
    : >"$lock"   # ours now: who we are, for the next waiter's line
    printf '%s @ %s (pid %s, since %s)\n' "$branch" "${tip:0:8}" "$$" "$(date '+%H:%M:%S')" >&7
fi
lock_wait="$(awk -v a="$lock_t0" -v b="${EPOCHREALTIME/,/.}" 'BEGIN { printf "%.1f", b - a }')"
goh_span_start   # the land's START (lib/budget.py KINDS): the lock is ours, the rebase and the gate follow
# Refuse BEFORE the gate what could never land (G15): a pre-commit hook that refused the commit left
# the tip at the base, and "landed" was printed over a no-op merge. Landing is a state change, so the
# state is checked, not the exit code of the merge.
base="$(git -C "$main" rev-parse HEAD)"
if [ "$tip" = "$base" ]; then
    err "land.sh: nothing to land -- $branch @ ${tip:0:8} IS the main tip (did a hook refuse the commit?) -- nothing landed"
    exit 1
fi
# Gate the commit main will fast-forward to: the branch on top of main as it is now, under the lock.
if ! git merge-base --is-ancestor "$base" "$tip"; then
    if ! git rebase -q "$base" 7>&- >/dev/null 2>&1 </dev/null; then
        git rebase --abort 7>&- >/dev/null 2>&1 </dev/null || true
        err "land.sh: $branch conflicts with main ${base:0:8} -- the rebase was aborted, $branch is left at ${tip:0:8}; rebase it by hand -- nothing landed"
        exit 1
    fi
    now="$(git rev-parse HEAD)"
    info "rebased $branch onto main ${base:0:8}: ${tip:0:8} -> ${now:0:8}"
    tip="$now"
    # The gates that judge the tip are the TIP's (GOH.VERDICTKEYDRIFT). Run from this checkout,
    # this script is the head export of the tip BEFORE the rebase (goh_from_head, above), and the
    # gate, the goh-landed identity and the loaded run's record key all came from that export:
    # b54b9d62 landed rebased from 380f48d0, its loaded verdict was recorded under 380f48d0's gates
    # (aa4f97f2) and the ship, reading under b54b9d62's, found none. Only when the rebased checkout
    # IS the gates checkout: a consumer's rebase moves no gates, and a caller's pinned export stays.
    if [ -f "$GOH/.goh-head" ] && [ -n "${GOH_LIVE_ROOT:-}" ] \
            && [ "$(cd "$GOH_LIVE_ROOT" 2>/dev/null && pwd -P)" = "$(cd "$here" && pwd -P)" ] \
            && goh_export_rev "$here" "$tip" "$tip"; then
        GOH="$_goh_export"
        export GOH_DIR="$GOH"
        goh_path_front PYTHONPATH "$GOH"
        info "the gates are ${tip:0:8}'s now: $GOH"
    fi
fi
info "gate: $gate"
# In the gate's environment only, never exported: an exported GOH_ key is gate bookkeeping and must be
# verdict-free (tests/test_verdict_free_keys.py), and this one is one process's setting.
# The gate runs under tools/load.py with 0 spinners, which admits it by a gate slot -- the one
# admission every gate takes, so a land and a push gate wait for the same slots (GOH.ONEADMISSION,
# docs/decisions/D-0008) -- taken HERE, under the land lock, never around this script: a land queued
# on the lock then holds nothing a ship holding the lock can be waiting on (GOH.LOCKORDER: land 20
# before gate_slot 50). 90 minutes bounds the gate as the coordinator's wrapper did. Class `ship`
# (GOH_GATE_CLASS, consumed by lib/admission.py): ahead of every `work` waiter (GOH.SLOTFAIR).
# GOH_TIMING_VIA: the push gate this runs is this land's, so it leaves the ledger row to land.sh.
land_timings="$(mktemp "${TMPDIR:-/tmp}/goh-land-timings.XXXXXX")" || land_timings=""
if ! GOH_PROFILE_PROVEN="${GOH_PROFILE_PROVEN-1}" GOH_LAND_VERDICTS_REUSE="$verdicts" GOH_GATE_CLASS=ship \
        GOH_TIMING_VIA=land GOH_TIMINGS="${GOH_TIMINGS:-$land_timings}" \
        "$GOH_PY" "$GOH/tools/load.py" --background-ok "land gate" \
        --max 90m 0 -- bash -c "$gate" 7>&- </dev/null; then
    err "land.sh: the gate failed on ${tip:0:8} -- nothing landed"
    exit 1
fi
now="$(git rev-parse HEAD)"
if [ "$now" != "$tip" ]; then
    err "land.sh: $branch moved while it was gated (${tip:0:8} -> ${now:0:8}) -- nothing landed; land again"
    exit 1
fi
base="$(git -C "$main" rev-parse HEAD)"   # re-read: main may have moved during a minutes-long gate
if ! git -C "$main" merge-base --is-ancestor "$base" "$tip"; then
    err "land.sh: main moved on to ${base:0:8} while ${tip:0:8} was gated, outside land.sh (its lock) -- land again"
    exit 1
fi
# What the range changes is known before main moves, so a missing change lands nothing.
changed="$(GIT_OPTIONAL_LOCKS=0 git -C "$main" diff --name-only "$base" "$tip")"
for want in ${GOH_LAND_EXPECT:-}; do
    case $'\n'"$changed"$'\n' in
        *$'\n'"$want"$'\n'*) ;;
        *)  err "land.sh: the landed range ${base:0:8}..${tip:0:8} does not change $want (GOH_LAND_EXPECT) -- the intended change is not in it"
            exit 1 ;;
    esac
done
# GOH_LAND_CALLER names this script to the merge's own hooks (lib/landed_only.sh refuses a commit on
# the landed-only branch from anyone else); inline, so it reaches the merge alone, never the gate.
GOH_LAND_CALLER=land.sh git -C "$main" merge --ff-only -q "$tip" 7>&-
# Verify the claim before making it: main must now BE the gated tip and differ from where it was.
after="$(git -C "$main" rev-parse HEAD)"
if [ "$after" != "$tip" ] || [ "$after" = "$base" ]; then   # (also: nothing to land if the tip IS main)
    err "land.sh: main is at ${after:0:8} after the merge, not the gated ${tip:0:8} (was ${base:0:8}) -- nothing landed"
    exit 1
fi
# The record a push reads (GOH_LANDED_ONLY): this SHA was gated here and merged. In the common git
# dir, so it outlives the worktree that landed it, and by SHA, so a branch that moved is not it.
# `<sha> <gate identity>`: the push's release rule trusts a parent only under the full gate's identity.
identity="$("$GOH_PY" "$GOH/lib/gate_identity.py" key --mode "$mode" </dev/null 7>&-)" || identity=unknown
printf '%s %s\n' "$tip" "$identity" >>"$(git -C "$main" rev-parse --path-format=absolute --git-common-dir)/goh-landed"
ok "landed ${tip:0:8} on $(git -C "$main" rev-parse --abbrev-ref HEAD) in $main (nothing pushed)"
exit
} # parse-guard
