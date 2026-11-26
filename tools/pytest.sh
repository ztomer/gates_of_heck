#!/usr/bin/env bash
# The gates_of_heck test suite, parallel under pytest-xdist. Arguments go to pytest
# (`tools/pytest.sh -k ship`, `tools/pytest.sh tests/test_ship.py`); none runs all of tests/.
# --dist loadgroup: unmarked tests spread by load; files sharing an
# xdist_group marker stay on ONE worker -- for SHARED state only. The release
# tests' group went 2026-10-06: test_release_hardening.py rewrites a copy of
# release.sh now, not the real one, and tests/_tree_guard.py fails any run in
# which a test moves the checkout.
# -n 12, the cap (a smaller share of the cores, fewer workers): measured 2026-10-06 against 8 (67/79 s vs 74/96 s, interleaved) once
# the spawn cuts landed; 16 was a draw with 12 under load, and leaves no
# headroom for the other sessions sharing the box.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/../lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
cd "$GOH"
# shellcheck source=gates/_pytest_root.sh
# ONE SUITE AT A TIME, never a suite inside itself (G2d, 2026-10-25). A test that ran this runner
# with a stub `python3` first on PATH stopped intercepting the day the runner moved to the house
# interpreter: the runner ran the REAL suite inside the test, whose copy of that test ran it again --
# 33 nested suites and ~590 processes, load ~229, until killed by hand. The marker is not GOH_-
# prefixed, so a hermetic test env that drops GOH_* keeps it; a test that means a nested run (with a
# stub interpreter through GOH_PY) says so with GATES_OF_HECK_SUITE_NESTED_OK=1.
if [ -n "${GATES_OF_HECK_SUITE_RUNNER:-}" ] && [ -z "${GATES_OF_HECK_SUITE_NESTED_OK:-}" ]; then
    die "tools/pytest.sh started inside a running goh suite (runner pid $GATES_OF_HECK_SUITE_RUNNER) -- a nested suite recurses; a test that means it sets GATES_OF_HECK_SUITE_NESTED_OK=1 and a stub GOH_PY" 2
fi
# ONE RUN PER LAND (GOH.SUITEONCE, lib/pre_reuse.py `suite`): a land's gate (GOH_LAND_VERDICTS_REUSE,
# and the base main it gates on beside it, `.base`, from tools/land.sh) whose checkout IS the
# commit tree its pre-step judged green, against that base, takes the pre-step's verdict for the
# whole run -- the diff's tests passed on these bytes, the rest on main's recorded tree -- and runs
# no pytest; the verdict's id goes to `.reused`, which the land's record names. A narrowed run, the
# instrumented coverage run, or any doubt runs the suite.
if [ "$#" -eq 0 ] && [ -s "${GOH_LAND_VERDICTS_REUSE:-}" ] && [ -s "$GOH_LAND_VERDICTS_REUSE.base" ]; then
    suite_base="" suite_why="" suite_rc=0
    IFS= read -r suite_base <"$GOH_LAND_VERDICTS_REUSE.base" || true
    suite_why="$("$GOH_PY" "$GOH/lib/pre_reuse.py" --root "$GOH" --suite "$GOH_LAND_VERDICTS_REUSE" \
        --base "$suite_base" </dev/null)" || suite_rc=$?
    if [ "$suite_rc" -eq 0 ] && [ -n "${suite_why#*$'\n'}" ] && [ "${suite_why#*$'\n'}" != "$suite_why" ]; then
        printf '%s\n' "${suite_why#*$'\n'}" >"$GOH_LAND_VERDICTS_REUSE.reused"
        # Every decision, either way, for the land's record (GOH.SUITEONCEMISS: tools/land.sh `.why`).
        printf 'reused: %s\n' "${suite_why%%$'\n'*}" >>"$GOH_LAND_VERDICTS_REUSE.why" || true
        ok "suite: the pre-step's verdict ${suite_why#*$'\n'} holds for this tree -- not run again: ${suite_why%%$'\n'*}"
        # The gate around it runs no suite: counted at its real width, not an equal share (GOH.PREWIDTH).
        "$GOH_PY" "$GOH/lib/land_share.py" suiteless </dev/null || true
        exit 0
    fi
    printf 'missed: %s\n' "${suite_why%%$'\n'*}" >>"$GOH_LAND_VERDICTS_REUSE.why" || true
    info "suite: the pre-step's verdict is not this run's (${suite_why%%$'\n'*}) -- the suite runs"
fi
# ONE SUITE PER CHECKOUT (G2e, 2026-10-25). Two suites of one tree at once share its bin/, its
# target/ and its proven cache, and each runs at half speed beside the other -- the full gate ran
# exactly that (a test step beside the coverage group's run) until G2e. A second top-level run is
# REFUSED, naming the holder, rather than queued: a wait looks like a hang. The lock is a symlink
# whose target is the holder's pid, made atomically by `ln -s`; a holder that is gone is reaped. A
# run nested on purpose (GATES_OF_HECK_SUITE_NESTED_OK) is inside the holder's run and takes none.
# GATES_OF_HECK_SUITE_LOCK names another lock (a test of this one).
if [ -z "${GATES_OF_HECK_SUITE_RUNNER:-}" ] && [ -z "${GATES_OF_HECK_SUITE_NESTED_OK:-}" ]; then
    # The checkout's OWN git dir (a worktree has its own), never a calling hook's GIT_DIR.
    SUITE_LOCK="${GATES_OF_HECK_SUITE_LOCK:-$( (unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR
        git -C "$GOH" rev-parse --absolute-git-dir 2>/dev/null) || printf %s "$GOH")/goh-suite.lock}"
    # The link names the holder's STAMP, `<pid> <start time>` (GOH.STRAYSIGNAL2): a killed run's
    # pid handed to another process since reads as gone, never as a suite holding the checkout.
    # shellcheck source=lib/pid_owner.sh
    . "$GOH/lib/pid_owner.sh"
    SUITE_STAMP="$(goh_pid_stamp $$)" || die "cannot read this suite's own start time (ps)" 2
    until ln -s "$SUITE_STAMP" "$SUITE_LOCK" 2>/dev/null; do
        holder="$(readlink "$SUITE_LOCK" 2>/dev/null || true)"
        if goh_pid_alive "$holder"; then
            die "another goh suite (pid ${holder%% *}) is running in this checkout -- one suite at a time ($SUITE_LOCK)" 2
        fi
        rm -f "$SUITE_LOCK"   # its holder is gone: a killed run's lock
    done
    # THE ONE ADMISSION (GOH.ADMITSUITE, after GOH.ONEADMISSION): a suite started outside a gate
    # comes in where every gate does (lib/admission.py `admit` through lib/gate_slot.sh: memory, then
    # a gate slot), after this checkout's lock, so a second suite here is still refused at once. It
    # asked only memory until 2026-11-17: a hand-run suite of up to 12 workers started beside the
    # two push gates holding both slots. A WHOLE run is `work` (the long lane, behind every push and
    # land); a narrowed one (a path argument) is `short`, the commit gate's lane, so a red/green loop
    # on one file waits behind commit gates only; GOH_GATE_CLASS overrides both. THE ADDED WAIT IS
    # BOUNDED: GOH_SUITE_WAIT_MAX seconds (default 900, load.py's --wait-max default; the memory
    # wait counted inside it), then exit 75 naming the holders -- never a run beside them, never a
    # wait that looks like a hang. Inside a gate (GOH_GATE_SLOT, or a holder among the ancestors)
    # the suite is that gate's and takes nothing. Pinned by tests/test_suite_admission.py.
    suite_class="${GOH_GATE_CLASS:-}"   # empty: the acquire derives it -- `lane` in a lane worktree, else `work` (GOH.LANECLASS)
    for a in "$@"; do
        [ -n "${GOH_GATE_CLASS:-}" ] || [ ! -e "${a%%::*}" ] || { suite_class=short; break; }
    done
    suite_wait="${GOH_SUITE_WAIT_MAX:-900}"
    case "$suite_wait" in
        '' | *[!0-9]*) die "GOH_SUITE_WAIT_MAX must be whole seconds (got '$suite_wait')" 2 ;;
    esac
    # shellcheck source=lib/gate_slot.sh
    . "$GOH/lib/gate_slot.sh"
    trap '[ "$(readlink "$SUITE_LOCK" 2>/dev/null)" = "$SUITE_STAMP" ] && rm -f "$SUITE_LOCK"; gate_slot_release' EXIT
    if [ -z "${GOH_GATE_SLOT:-}" ]; then
        gate_slot_acquire "goh suite ($PWD)" "$suite_class" "$suite_wait" \
            || die "the goh suite was not admitted within GOH_SUITE_WAIT_MAX=${suite_wait}s (holders above) -- nothing ran; rerun later, or raise GOH_SUITE_WAIT_MAX" 75
    fi
    # MEMORY ADMISSION (GOH.MEMADMIT): with the gate slots off (GOH_GATE_SLOTS=0) or inside a gate
    # found by ancestry, the suite still waits, bounded, while the machine is short of memory; a slot
    # taken above asked memory first.
    [ -n "${GOH_GATE_SLOT:-}" ] || "$GOH_PY" "$GOH/lib/mem_admit.py" wait --what "the goh suite"
    # THE DURATION TABLE (GOH.DURATIONSSTALE): a top-level run, and only one, is named goh's state
    # database, goh.db (lib/fast_tests.py `cache()`, its one spelling; GOH.DURATIONSDB), and
    # tests/suite_home.py pops it, so no worker or fixture run under this one writes it. A run
    # nested on purpose is named none.
    preflight_args=(--table)
else
    unset GOH_TEST_DURATIONS
    preflight_args=()
fi
# WHAT THE SUITE REFUSES TO START WITHOUT, ASKED ONCE (GOH.PYHARNESS, lib/suite_preflight.py): every
# goh-suite module imports and is the manifest's pin, the release hosted CI installs (GOH.PYTESTPIN:
# pytest 9.2.0 renamed a long string parameter's id; CI, unpinned, got it while this box ran 9.1.1,
# and v0.139.6's CI was red on a tree the local gate passed); every ruff on the machine is goh's pin
# (gates/ruff_agree.py, GOH.TOOLHYGIENE, GOH.RUFFVERSION). One process, 0.3 s, where eleven took
# 1.9. It answers the duration table, the pins' pytest plugins and a narrowed run's width too.
# GOH_LIVE: the pins judged are this tree's, as its tests are (tests/conftest.py). Before the root
# is claimed: a refused run leaves none.
preflight="" preflight_rc=0 preflight_at="" suite_width="" PYTEST_PLUGINS=""
preflight="$(GOH_LIVE=1 "$GOH_PY" "$GOH/lib/suite_preflight.py" "${preflight_args[@]}" -- "$@" </dev/null)" \
    || preflight_rc=$?
[ "${#preflight_args[@]}" -eq 0 ] || GOH_TEST_DURATIONS=""
while IFS=$'\t' read -r key value; do
    case "$key" in
        at) preflight_at="$value" ;;
        refuse) die "${value#* }" "${value%% *}" ;;
        table) GOH_TEST_DURATIONS="$value" ;;
        plugins) PYTEST_PLUGINS="$value" ;;
        width) suite_width="$value" ;;
    esac
done <<<"$preflight"
# Killed, not refused: an import that takes the interpreter down says no word of its own.
[ "$preflight_rc" -eq 0 ] || die "the suite's preflight died (exit $preflight_rc)${preflight_at:+ importing $preflight_at for the house interpreter $GOH_PY -- \"$GOH_PY\" -m pip install $preflight_at} -- lib/suite_preflight.py"
[ "${#preflight_args[@]}" -eq 0 ] || export GOH_TEST_DURATIONS
# ONE PLUGIN SET (GOH.PLUGINPARITY): this run loads only the plugins pyproject.toml names with -p, and
# a pytest a test starts in a FIXTURE repo loads only the manifest's pins' (PYTEST_PLUGINS), never
# the box's other site-packages -- the set hosted CI, which installs exactly the pins, auto-loads.
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_PLUGINS
. "$GOH/gates/_pytest_root.sh"
# After the guards: a refused runner claims no root and cuts no other run's kept ones.
goh_pytest_root  # concurrent suites cannot prune each other's temp dirs; kept after a failure
export GATES_OF_HECK_SUITE_RUNNER=$$
# The tools the suite's FIXTURE repos are shipped with (run 42944992082: 23 test_ship tests on a
# missing git-cliff): goh's own gates declare no ship layer, so the goh-suite layer installs it.
command -v git-cliff >/dev/null 2>&1 || die "git-cliff is not on PATH (tools/ship.py's next version) -- brew install git-cliff"
# What goh's OWN pre-commit runs (test_self_hosted_repo_satisfies_its_own_hook; run 42957429139 died
# "swiftlint is not installed"): the shell lint, the swift twin and the rustfmt check.
command -v shellcheck >/dev/null 2>&1 || die "shellcheck is not on PATH (goh's own hook: shell lint) -- brew install shellcheck"
command -v swiftlint >/dev/null 2>&1 || die "swiftlint is not on PATH (goh's own hook: the swift twin) -- brew install swiftlint"
command -v rustfmt >/dev/null 2>&1 || die "rustfmt is not on PATH (goh's own hook: the rustfmt check) -- rustup component add rustfmt"
ARGS=(-q)
# THIS GATE'S SHARE of the cores, capped at 12 (run 42950563400: `-n 12` on a 3-core hosted
# runner starved a cold goh build and a structural --full past their 600 s timeouts). Never the
# machine (GOH.POOLSHARE, 2026-11-17: a suite sized to all 16 cores beside four other gates each
# sized the same put ~220 threads on 16 cores): inside a gate, its admission's GOH_GATE_WORKERS;
# outside one (a land's pre-step), cores / (the gates admitted now + this run).
# THE SHARE IS LIVE (GOH.SHAREGROW, lib/worker_share.py): xdist starts at the ceiling (12, or the
# machine's cores when fewer) and each worker takes a token from the pool below before each
# test, so the run is as wide as cores / the gates admitted NOW -- it grows the moment another
# gate leaves (the SELECTAUDIT land ran at -n 4 for 11+ minutes on a box at load 7.9 of 16,
# 2026-11-17) and shrinks when one is admitted. GOH_SUITE_CORES pins a fixed count, no pool.
# A NARROWED RUN TAKES WHAT ITS TESTS COST (GOH.PYHARNESS, lib/suite_width.py): one file's 2 s of
# tests started 12 workers and spent 10 CPU seconds on them. `-n 0` (or the few the table says) is
# under any share, so a serial run takes no pool. A run it cannot cost is at the ceiling, as a whole
# one; GOH_SUITE_CORES and the caller's own -n (after these arguments) win.
# shellcheck source=lib/worker_share.sh
. "$GOH/lib/worker_share.sh"
if [ -n "${GOH_SUITE_CORES:-}" ]; then
    cores="$GOH_SUITE_CORES" suite_width=""
elif [ "$suite_width" = 0 ]; then
    cores=1
else
    goh_share_pool "$PYTEST_DEBUG_TEMPROOT/share-pool" 12 || die "cannot start the suite's live share (lib/worker_share.py pool)" 2
    cores="$GOH_SHARE_CEIL"
fi
case "$cores" in '' | *[!0-9]* | 0) die "cannot read this machine's core count (got '$cores')" 2 ;; esac
[ "$cores" -gt 12 ] && cores=12
case "$suite_width" in
    *[!0-9]*) die "lib/suite_width.py answered no worker count ('$suite_width')" 2 ;;
    ?*) [ "$suite_width" -gt "$cores" ] || [ "$suite_width" = 0 ] || cores="$suite_width" ;;
esac
[ "$suite_width" != 0 ] || cores=0
ARGS+=(-n "$cores" --dist loadgroup)
# ONE SHARD OF THE SUITE (GOH.CIWALL): hosted CI's coverage parts each run the K-th of N disjoint
# parts (tests/_shard.py); only the coverage gate's part mode sets it (gates/_coverage_rust.sh). A
# shard is never a land's suite: its verdict record would claim tests it never ran.
if [ -n "${GOH_SUITE_SHARD:-}" ]; then
    [ -z "${GOH_LAND_VERDICTS_RECORD:-}${GOH_LAND_VERDICTS_REUSE:-}" ] \
        || die "GOH_SUITE_SHARD=$GOH_SUITE_SHARD with a land's verdict record: a shard is part of the suite, never a land's" 2
    info "suite shard $GOH_SUITE_SHARD: this run is one part of the suite (tests/_shard.py)"
fi
# The caller's arguments reach pytest (GOH.HOOKSEED): `tools/pytest.sh -k plural` selects, and
# `tools/pytest.sh tests/test_ship.py` runs that file. They were dropped, and every narrowed run was
# the whole suite. tests/ is the target unless an argument names a path of its own (`file::node`).
TARGET=(tests/)
for a in "$@"; do
    [ -e "${a%%::*}" ] && { TARGET=(); break; }
done
# THE SUITE'S CPU-S (GOH.GATEBUDGET2, lib/budget.py KINDS `suite`): a WHOLE run (no argument)
# is a `goh suite` row in the timing ledger, `goh suite:carried` when it reused another run's
# verdicts (GOH_LAND_VERDICTS_REUSE); its wall moves with the box's load, its cpu-s does not. A
# narrowed run measures a selection and writes none. The ledger never fails the suite.
# shellcheck source=lib/timing_ledger.sh
. "$GOH/lib/timing_ledger.sh"
suite_kind=suite
[ ! -s "${GOH_LAND_VERDICTS_REUSE:-}" ] || suite_kind=suite:carried
[ -z "${GOH_SUITE_SHARD:-}" ] || suite_kind=suite:shard
# A LAND'S ONE RUN (GOH.LANDONERUN): the lander runs no pre-land beside its gate when the gate's
# suite reuses the enqueue's record (lib/land_par.sh), so this run fails fast in its place: the tree
# readers are handed out first (tests/_schedule.py `judges`) and the first red stops it.
if [ "$#" -eq 0 ] && [ -s "${GOH_LAND_VERDICTS_REUSE:-}" ]; then
    ARGS+=(--maxfail=1)
    export GATES_OF_HECK_SUITE_JUDGES_FIRST=1
fi
# THE RUN'S FILE EVENTS (GOH.FSCHURN2, tests/_fs_churn.py): an argument-less run counts the FSEvents
# records under its own roots; the count joins its ledger row (`fs_events`, `fs_dirs`, and `fs_tests`
# and `fs_whole`, so a ceiling is re-set from whole rows). Its per-run ceiling
# (tests/fs_events_ceiling.json) judges only a WHOLE run: a carried run (suite:carried, the rust
# gate's external pytest inside a land) deselects what the pre-step passed, and the change's own
# tests are no sample of it (GOH.FSCEILINGRED). A land's pre-step (arguments, and its verdict record)
# is counted as well (GOH.FSEVQUIET), judged by its tests' and files' ceilings, never the means, and
# a caller that names the file (GATES_OF_HECK_FS_EVENTS) gets any narrowed run counted.
[ "$#" -ne 0 ] && [ -z "${GOH_LAND_VERDICTS_RECORD:-}" ] || export GATES_OF_HECK_FS_EVENTS="${GATES_OF_HECK_FS_EVENTS:-$PYTEST_DEBUG_TEMPROOT/fs-events.json}"
[ "$#" -ne 0 ] || goh_span_start
rc=0
# A lane/work suite this script admitted runs under its class's QoS clamp (GATE_SLOT_QOS,
# lib/gate_slot.sh, GOH.LANDSHARE); inside a gate it inherits that gate's.
${GATE_SLOT_QOS[@]+"${GATE_SLOT_QOS[@]}"} "$GOH_PY" -m pytest "${TARGET[@]}" "${ARGS[@]}" "$@" || rc=$?
fs_fields=()
if [ "$#" -eq 0 ] && [ -s "${GATES_OF_HECK_FS_EVENTS:-}" ]; then
    mapfile -t fs_fields < <("$GOH_PY" -c 'import json, sys
r = json.load(open(sys.argv[1]))
if "events" in r:
    print("--field", "fs_events=%d" % r["events"], "--field", "fs_dirs=%d" % r["dirs_created"],
          "--field", "fs_tests=%d" % r.get("tests_run", 0), "--field", "fs_whole=%d" % bool(r.get("whole")),
          sep="\n")' "$GATES_OF_HECK_FS_EVENTS" 2>/dev/null || true)
    # The last report, kept: `python3 tests/_fs_ceilings.py REPORT` lowers the lists by a whole one.
    cp -f "$GATES_OF_HECK_FS_EVENTS" "${XDG_CACHE_HOME:-$HOME/.cache}/goh/fs-events-last.json" 2>/dev/null || true
fi
[ "$#" -ne 0 ] || goh_span_record "$suite_kind" goh "$([ "$rc" -eq 0 ] && echo 1 || echo 0)" --field "exit=$rc" "${fs_fields[@]}"
# A SATURATED fseventsd IS NAMED (GOH.FSEVCHURN): it sat at 6.9 GB after 2 d 18 h (2026-11-23) and
# the run only read it as slow. A whole run ends with its RSS judged: quiet under 1 GB, else one `✗`
# line with the restart command (the owner's call). A fault of the machine, not the tree: no exit.
[ "$#" -ne 0 ] || "$GOH_PY" -S "$GOH/lib/fseventsd_health.py" || true
if [ "$rc" -eq 0 ]; then
    goh_rm_tree "$PYTEST_DEBUG_TEMPROOT"
else
    info "the failed run's temp root is kept: $PYTEST_DEBUG_TEMPROOT"
fi
(exit "$rc")
exit
} # parse-guard
