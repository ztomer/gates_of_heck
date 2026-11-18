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
verdicts="" pre_start="" land_timings="" lock_wait=0 pre_wall="" pre_run="" pre_workers="" pre_reused="" suite_reused=""
# shellcheck disable=SC2329  # invoked by the EXIT trap below
land_exit() {
    local rc=$?
    rm -f "$verdicts" "$pre_start"
    [ -z "$verdicts" ] || rm -f "$verdicts.base" "$verdicts.reused"
    [ -z "${GOH_LAND_PRE_ONLY:-}${GOH_LAND_STACK_ONTO:-}" ] || return "$rc"   # the enqueue's pre-step run, a stacking rebase: no land, no ledger row
    [ ! -s "${land_timings:-}" ] && rm -f "${land_timings:-}" "${land_timings:-}.log" "${land_timings:-}.controls.log"
    goh_span_record land "$(goh_repo_id "${main:-.}")" "$([ "$rc" -eq 0 ] && echo 1 || echo 0)" \
        ${land_timings:+--timings "${GOH_TIMINGS:-$land_timings}"} --field "wait_lock_s=$lock_wait" \
        --field "exit=$rc" ${pre_wall:+--field "pre_s=$pre_wall"} \
        ${pre_run:+--field "pre_run_s=$pre_run" --field "pre_workers=$pre_workers"} \
        ${pre_reused:+--field "pre_reused=$pre_reused"} ${suite_reused:+--field "suite_reused=$suite_reused"} --field "sha=$(printf %.12s "${tip:-}")" || true
}
trap land_exit EXIT
# SPECULATIVE (GOH.LANDBISECTPAR, lib/landq_spec.py): the batch gates a red stack's second half on
# main beside the first. GOH_LAND_HOLD_FD names a pipe it reads from: the branch is never rebased
# (main left the base it was stacked on: exit 3), and a green gate waits on that pipe for `go`
# before main moves; anything else, or the batch gone (EOF), lands nothing, exit 3. Moved to fd 8
# before fd 7 is the lock's, closed for every long child, and never in the gate's environment.
hold=""
if [ -n "${GOH_LAND_HOLD_FD:-}" ]; then
    exec 8<&"$GOH_LAND_HOLD_FD"
    [ "$GOH_LAND_HOLD_FD" = 8 ] || exec {GOH_LAND_HOLD_FD}<&-
    hold=8
    unset GOH_LAND_HOLD_FD
fi

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
mode="${gate:-full}"   # an empty gate is the kit's push gate under the gates that judge (land_gate)

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
# it nests no lock (GOH.LOCKORDER). Bounded by its share (lib/pre_deadline.py: 10-25 min); a repo that declares none has no pre-step.
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
# land_rebase -- the branch on top of main as it is now: sets base, moves tip, and sets rebased=1
# when it had to move. A conflict aborts the rebase and lands nothing. STACKED (GOH_LAND_STACK_ONTO,
# below): onto that commit instead, carrying only the branch's own commits above
# GOH_LAND_STACK_UPSTREAM -- a commit of a batch item dropped from below it is not carried.
land_rebase() {
    rebased=0
    local upon=() below=""
    base="${GOH_LAND_STACK_ONTO:-$(git -C "$main" rev-parse HEAD)}"
    if [ -n "${GOH_LAND_STACK_ONTO:-}" ]; then
        [ "${GOH_LAND_STACK_UPSTREAM:-}" != "$base" ] || return 0
        upon=(--onto "$base" "${GOH_LAND_STACK_UPSTREAM:?GOH_LAND_STACK_ONTO needs GOH_LAND_STACK_UPSTREAM}")
        below=" plus the batch stacked on it below $branch"
    else
        git merge-base --is-ancestor "$base" "$tip" && return 0
        if [ -n "$hold" ]; then
            info "speculative: main ${base:0:8} is not below ${tip:0:8} any more -- not rebased, nothing landed"
            exit 3
        fi
        upon=("$base")
    fi
    # The registries every lane appends to merge by ROW (.gitattributes `merge=goh-rows`,
    # lib/row_merge.py, GOH.LANDUNION): two lanes' independent rows were a red and a full queue
    # wait (3 on 2026-11-17); two edits to one row still conflict. A repo's own config need not
    # define the driver: it is defined here, for this rebase only.
    rows=()
    if [ -f "$GOH/lib/row_merge.py" ]; then
        printf -v driver '%q %q %%O %%A %%B %%P' "$GOH_PY" "$GOH/lib/row_merge.py"
        rows=(-c "merge.goh-rows.driver=$driver")
    fi
    if ! git "${rows[@]}" rebase -q "${upon[@]}" 7>&- >/dev/null 2>&1 </dev/null; then
        conflicted="$(GIT_OPTIONAL_LOCKS=0 git diff --name-only --diff-filter=U 7>&- 2>/dev/null </dev/null | paste -sd ' ' -)" || conflicted=""
        git rebase --abort 7>&- >/dev/null 2>&1 </dev/null || true
        err "land.sh: $branch conflicts with main ${base:0:8}$below in ${conflicted:-(unnamed)} -- the rebase was aborted, $branch is left at ${tip:0:8}; rebase it by hand -- nothing landed"
        exit 1
    fi
    now="$(git rev-parse HEAD)"
    info "rebased $branch onto main ${base:0:8}: ${tip:0:8} -> ${now:0:8}"
    tip="$now"
    rebased=1   # the gates that judge the new tip are chosen before the gate (land_roots, below)
}
# land_pre [CMD RECORD] -- the pre-step (or CMD, recording into RECORD) on the tip as it is now;
# exits 1 on a red set (THE PRE-STEP, above). A green full run leaves its tip in pre_judged.
land_pre() {
    local cmd="${1:-$pre}" record
    [ -n "$verdicts" ] || verdicts="$(mktemp "$(goh_tmp)/goh-land-verdicts.XXXXXX")" || exit 2
    record="${2:-$verdicts}"
    rm -f "$record"   # the pre-step's suite writes it whole, or there is no record
    [ -n "${1:-}" ] || pre_judged=""
    pre_base="$(git -C "$main" rev-parse HEAD)"
    info "pre-land: $cmd"
    # The SHORT lane (GOH.PRESTEPADMIT): the commit gate's slots, which no long gate takes, so the
    # pre-step starts while long gates hold every long slot -- a land queues for a long slot ONCE,
    # for its gate. As a `ship` gate it queued twice, and a lane that would go red waited up to
    # 632 s first (2026-11-16). The wait is printed apart from the run: the step stamps its start.
    [ -n "$pre_start" ] || pre_start="$(mktemp "$(goh_tmp)/goh-land-prestart.XXXXXX")" || exit 2
    : >"$pre_start"
    # THE DEADLINE FOLLOWS THE SHARE (GOH.PRESTEPSHARE, lib/pre_deadline.py): taken after the
    # admission, from the share it gave and the worker-seconds the ledger measured; GOH_LAND_PRE_MAX
    # is its floor (a wide share), GOH_LAND_PRE_CEIL its ceiling, load.py's --max the backstop.
    pre_max="${GOH_LAND_PRE_MAX:-10m}" pre_ceil="${GOH_LAND_PRE_CEIL:-25m}"   # seams a test can reach
    pre_back="$("$GOH_PY" "$GOH/lib/pre_deadline.py" show --workers 1 --floor "$pre_max" --ceiling "$pre_ceil")" || exit 2
    pre_t0=$(date +%s)
    pre_rc=0
    (cd "$here" && GOH_LAND_VERDICTS_RECORD="$record" GOH_GATE_CLASS=short "$GOH_PY" "$GOH/tools/load.py" \
        --background-ok "pre-land" --max "$((pre_back + 60))" 0 -- \
        "$GOH_PY" "$GOH/lib/pre_deadline.py" run --mark "$pre_start" --floor "$pre_max" --ceiling "$pre_ceil" \
        -- bash -c "$cmd" pre-land "$pre_base" </dev/null 7>&- 8<&-) || pre_rc=$?
    pre_t1=$(date +%s)
    pre_s="" pre_workers="" pre_limit="" pre_run=""
    read -r pre_s pre_workers pre_limit <"$pre_start" 2>/dev/null || true
    [[ "$pre_s" =~ ^[0-9]+$ ]] || pre_s=$pre_t1   # never started: all of it was the wait
    pre_wall=$((${pre_wall:-0} + pre_t1 - pre_t0))
    pre_took="in $((pre_t1 - pre_t0)) s: waited $((pre_s - pre_t0)) s, ran $((pre_t1 - pre_s)) s of ${pre_limit:-?} s at ${pre_workers:-?} workers"
    # A run that reached a verdict is the ledger's measure of what the set costs (lib/pre_deadline.py WORK).
    if [ "$pre_rc" -ne 124 ] && [[ "$pre_workers" =~ ^[0-9]+$ ]]; then
        pre_run=$((pre_t1 - pre_s))
    fi
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
    elif [ -f "$here/tests/_land_verdicts.py" ] && [ ! -s "$record" ]; then
        # A tree whose suite records (tests/_land_verdicts.py) owes a record from a green pre-step:
        # for days tests/suite_home.py dropped GOH_LAND_VERDICTS_RECORD, every land went green with
        # none, and reuse and carry were inert with nothing said (GOH.VERDICTCARRY2). A pre-step
        # that ran no test says so in the record (tools/affected_tests.py); none, or empty, is a fault.
        err "land.sh: the pre-land tests were green on ${tip:0:8} $pre_took but wrote no verdict record ($record) although tests/_land_verdicts.py records one -- GOH_LAND_VERDICTS_RECORD did not reach the suite (tests/suite_home.py CALLER_INPUTS?) -- nothing landed"
        exit 1
    else
        ok "pre-land green $pre_took"
        [ -n "${1:-}" ] || pre_judged="$tip"
    fi
}
# THE TIP MAIN WILL HOLD (GOH.PREPARITY class 3): a branch behind main is rebased BEFORE its pre-step,
# outside the lock (the rebase moves the branch, never main). The pre-step judged the tip as the lane
# left it and the rebase came after, under the lock: PREPARITY's land went green at 3709f571, was
# rebased onto a main holding gates/_proven.sh at 500 lines, and went red in the full gate at 501;
# every lane older than a ship failed changelog_tags so (REUSEAUDIT2 dd8403da). The enqueue's run
# (GOH_LAND_PRE_ONLY) moves nothing: the queue holds the sha the lane enqueued.
rebased=0
# STACKED (GOH.LANDBATCH, tools/landq.py's batch, lib/landq_batch.py): the lander, holding the land
# lock, stacks several lanes' branches -- each rebased onto the one below it -- and gates the tip
# once. Each branch is moved by this script's own rebase (the row merge driver with it), then it
# stops: no pre-step, no lock, no gate.
if [ -n "${GOH_LAND_STACK_ONTO:-}" ]; then
    land_rebase
    exit 0
fi
[ -n "${GOH_LAND_PRE_ONLY:-}" ] || land_rebase
# THE ENQUEUE'S VERDICT (GOH.PRELANDTWICE, lib/land_pre_reuse.sh): a kept green verdict is reused
# where the tip moved only in paths no test reads.
# shellcheck source=lib/land_pre_reuse.sh
. "$GOH/lib/land_pre_reuse.sh" || exit 2
land_pre_from
[ -z "$pre" ] || land_pre_again "the enqueue"
# GOH_LAND_PRE_ONLY (GOH.ENQUEUEPRE): `goh land --enqueue` runs the pre-step in the caller's worktree
# before it queues the tip -- this script, so the set, the lane and the deadline are the lander's own
# -- and stops here: no lock, no gate, no fast-forward. 0 is green or no verdict, 1 is a red set.
if [ -n "${GOH_LAND_PRE_ONLY:-}" ]; then
    land_pre_keep
    exit 0
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
# Main moved again while the pre-step ran (another lane landed): the rebased tip is pre-stepped
# again, before the gate, so the red only the merged tree has is still the pre-step's (GOH.PREPARITY).
land_rebase
if [ "$rebased" = 1 ] && [ -n "$pre" ]; then
    info "pre-land: main moved during the pre-step -- again on ${tip:0:8}"
    land_pre_again "this land's"
fi
# THE GATES THAT JUDGE A GOH LAND (GOH.PRESTEPHEAD, docs/decisions/D-0012). The queue drives every
# land with main's land.sh (tools/landq.py land_script), but the gate code is the tree's own when the
# tree IS the gates' repo, so a lane that weakened a gate was judged by its own weakened copy. When
# this tree shares the driver's root commit and its diff from main changes the gate code (the gate
# identity's content, lib/gate_identity.py) or .gatesrc, the gate runs twice: under MAIN tip's
# gates (the judge, its land gate read from main's .gatesrc), then under the tip's own, so a gate
# change still proves itself before main moves. A diff that changes neither has one gate code: one
# run, under the tip's. Either way the tip's gates key the verdicts and the goh-landed line
# (GOH.VERDICTKEYDRIFT: b54b9d62 landed rebased from 380f48d0 with its loaded verdict recorded under
# 380f48d0's gates, and the ship, reading under b54b9d62's, found none). A consumer's land runs the
# kit's gates as they are. Pinned by tests/test_land_driver.py and tests/test_land_identity.py.
goh_root_of() { if [ -s "$1/.goh-root" ]; then cat "$1/.goh-root"; elif [ -e "$1/.git" ]; then git -C "$1" rev-list --max-parents=0 HEAD; fi; }
land_roots() {
    tip_root="$GOH" tip_who="the kit" judge_root="" judge_gate=""
    [ "$(git rev-list --max-parents=0 "$tip" 2>/dev/null)" = "$(goh_root_of "$GOH" 2>/dev/null)" ] || return 0
    local k_tip k_main
    if ! { goh_export_rev "$here" "$tip" "$tip" && tip_root="$_goh_export" && goh_export_rev "$here" "$base" "$base" \
            && k_tip="$("$GOH_PY" "$GOH/lib/gate_identity.py" key --mode land --root "$tip_root" </dev/null 7>&-)" \
            && k_main="$("$GOH_PY" "$GOH/lib/gate_identity.py" key --mode land --root "$_goh_export" </dev/null 7>&-)"; }; then
        err "land.sh: cannot export or key the gates of ${tip:0:8} and main ${base:0:8} -- nothing landed"
        exit 1
    fi
    tip_who="${tip:0:8}"
    if [ "$k_tip" != "$k_main" ] || ! git diff --quiet "$base" "$tip" -- .gatesrc 7>&-; then
        judge_root="$_goh_export"
        judge_gate="${GOH_LAND_GATE:-}"
        if [ -z "${GOH_LAND_GATE:-}" ] && [ -f "$main/.gatesrc" ]; then
            judge_gate="$(cd "$main" && bash -c 'unset GOH_LAND_GATE; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_GATE-}"')" \
                || { err "land.sh: cannot read $main/.gatesrc -- nothing landed"; exit 1; }
        fi
        info "the lane changes the gate code: main ${base:0:8}'s gates judge ${tip:0:8} first, then its own"
    fi
}
land_roots
# In the gate's environment only, never exported: an exported GOH_ key is gate bookkeeping and must be
# verdict-free (tests/test_verdict_free_keys.py), and this one is one process's setting.
# The gate runs under tools/load.py with 0 spinners, which admits it by a gate slot -- the one
# admission every gate takes, so a land and a push gate wait for the same slots (GOH.ONEADMISSION,
# docs/decisions/D-0008) -- taken HERE, under the land lock, never around this script: a land queued
# on the lock then holds nothing a ship holding the lock can be waiting on (GOH.LOCKORDER: land 20
# before gate_slot 50). 90 minutes bounds the gate as the coordinator's wrapper did. Class `ship`
# (GOH_GATE_CLASS, consumed by lib/admission.py): ahead of every `work` waiter (GOH.SLOTFAIR).
# GOH_TIMING_VIA: the push gate this runs is this land's, so it leaves the ledger row to land.sh.
land_timings="$(mktemp "$(goh_tmp)/goh-land-timings.XXXXXX")" || land_timings=""
# land_gate ROOT WHOSE GATE -- the gate (empty: ROOT's push gate) under ROOT's gates; red exits 1.
land_gate() {
    local cmd="${3:-$1/tools/gate_profile.sh .}"
    info "gate: $cmd (under $2's gates)"
    # An export's gates are put first; a consumer's land (ROOT is the kit) keeps the caller's GOH_DIR
    # and PYTHONPATH, the ones the push after it runs under: forced to the kit, the proof keyed roots
    # the push's key did not, and the push re-ran what the land had proven (GOH.PRESTEPHEAD's
    # 8150234a; tests/test_proven.py::test_land_leaves_a_proof_the_push_that_follows_reuses).
    local over=()
    [ "$1" = "$GOH" ] || over=(GOH_DIR="$1" PYTHONPATH="$(goh_path_front PYTHONPATH "$1"; printf %s "$PYTHONPATH")")
    if ! env ${over[@]+"${over[@]}"} GOH_PROFILE_PROVEN="${GOH_PROFILE_PROVEN-1}" GOH_LAND_VERDICTS_REUSE="$verdicts" \
            GOH_GATE_CLASS=ship GOH_TIMING_VIA=land GOH_TIMINGS="${GOH_TIMINGS:-$land_timings}" \
            "$GOH_PY" "$1/tools/load.py" --background-ok "land gate" \
            --max 90m 0 -- bash -c "$cmd" 7>&- 8<&- </dev/null; then
        err "land.sh: the gate failed on ${tip:0:8} under $2's gates -- nothing landed"
        exit 1
    fi
}
# ONE RUN PER LAND (GOH.SUITEONCE): the gate's suite takes the pre-step's verdict when it judged
# this very tree against this base (tools/pytest.sh, lib/pre_reuse.py `suite`); it reads the base
# here and leaves the verdict's id in `.reused`, which the land's record names (`suite_reused`).
[ ! -s "$verdicts" ] || printf '%s\n' "$base" >"$verdicts.base"
[ -z "$judge_root" ] || land_gate "$judge_root" "main ${base:0:8}" "$judge_gate"
land_gate "$tip_root" "$tip_who" "$gate"
[ -z "$verdicts" ] || [ ! -s "$verdicts.reused" ] || IFS= read -r suite_reused <"$verdicts.reused" || true
if [ "$tip_root" != "$GOH" ]; then   # the tip's gates key the goh-landed line (GOH.VERDICTKEYDRIFT)
    GOH="$tip_root"; export GOH_DIR="$GOH"; goh_path_front PYTHONPATH "$GOH"
fi
if [ -n "$hold" ]; then
    info "speculative gate green on ${tip:0:8} -- waiting for the batch's word"
    word=""
    IFS= read -r word <&8 || true
    exec 8<&-
    if [ "$word" != go ]; then
        info "speculative green on ${tip:0:8} not used: the batch's base is not main's (${word:-no word}) -- nothing landed"
        exit 3
    fi
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
