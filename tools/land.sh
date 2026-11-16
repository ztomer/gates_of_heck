#!/usr/bin/env bash
# land.sh -- from a worktree: gate the branch tip, then fast-forward the main checkout to THAT sha.
#
# The house workflow lands a branch only after its own push gate is green on the tip
# (the kit's `tools/gate_profile.sh .`, by path). Done by hand it was `gate; merge`, and on 2026-10-08 a `;` where
# `&&` belonged moved main onto a red gate run (engineering rule #3: gates are structural, not
# disciplinary). So the two are one command:
#
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
# LOCK ORDER (GOH.LOCKORDER, lib/lock_order.tsv). The land lock comes BEFORE the load slot: this
# script takes the land lock, then runs the gate in the slot itself (`tools/load.py 0 --`). It is
# run bare, `bash tools/land.sh`; run inside the slot (`load.py 0 -- bash tools/land.sh`) it is
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
# The land lock (GOH.LANDSERIAL, header): fd 7, held until this script exits.
# shellcheck source=lib/lock_order.sh
. "$GOH/lib/lock_order.sh" || exit 1
lock="$(git -C "$main" rev-parse --path-format=absolute --git-common-dir)/goh-land.lock"
exec 7>>"$lock"
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
    # The order first (GOH.LOCKORDER): a land inside a holder of a later lock -- the load slot of
    # `load.py 0 -- land.sh`, the deadlock of 2026-11-15 -- is refused before it can queue.
    if ! goh_lock_check land "$lock"; then
        err "land.sh: run it bare, \`bash tools/land.sh\`: it takes the land lock first and the load slot for its gate after -- nothing landed"
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
fi
info "gate: $gate"
# In the gate's environment only, never exported: an exported GOH_ key is gate bookkeeping and must be
# verdict-free (tests/test_verdict_free_keys.py), and this one is one process's setting.
# The gate runs in the load slot (tools/load.py, 0 spinners: a place shared with other gates), taken
# HERE, under the land lock, never around this script: a land queued on the lock then holds nothing
# a ship holding the lock can be waiting on (GOH.LOCKORDER: land 20 before load 30). 90 minutes
# bounds the gate as the coordinator's wrapper did.
if ! GOH_PROFILE_PROVEN="${GOH_PROFILE_PROVEN-1}" "$GOH_PY" "$GOH/tools/load.py" --background-ok "land gate" \
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
# THE LOADED SUITE LEAVES THE SHIP (GOH.SHIPRELREUSE R2). A repo that declares GOH_LAND_LOADED (goh:
# tools/loaded_suite.sh) has it started now on the tip just landed, in the background, its verdict
# recorded by tree (lib/loaded_verdict.py): the release cut from this tip reads it instead of
# running it. The runner outlives this script in a session of its own; its load.py accounts and
# reaps every spinner. A start that fails is said, never a failed land: the ship refuses the tip.
loaded=""
if [ -f "$here/.gatesrc" ]; then
    loaded="$(cd "$here" && bash -c 'unset GOH_LAND_LOADED; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_LOADED-}"')" || loaded=""
fi
if [ -n "$loaded" ] && [ -n "${GATES_OF_HECK_SUITE_RUNNER:-}" ]; then
    # A land run by a test of goh's own suite (a clone carrying this .gatesrc) must not start a
    # second suite under load behind the first: tools/pytest.sh refuses a nested suite anyway.
    info "land.sh: inside a running goh suite (pid $GATES_OF_HECK_SUITE_RUNNER) -- the loaded run of ${tip:0:8} is not started"
elif [ -n "$loaded" ]; then
    "$GOH_PY" "$GOH/lib/loaded_verdict.py" start --repo "$main" --sha "$tip" --cmd "$loaded" 7>&- </dev/null \
        || warn "land.sh: the loaded run of ${tip:0:8} did not start -- a ship of it will refuse until it has a verdict"
fi
exit
} # parse-guard
