#!/usr/bin/env bash
# The gates_of_heck test suite: parallel under pytest-xdist when installed. Arguments go to pytest
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
    until ln -s "$$" "$SUITE_LOCK" 2>/dev/null; do
        holder="$(readlink "$SUITE_LOCK" 2>/dev/null || true)"
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            die "another goh suite (pid $holder) is running in this checkout -- one suite at a time ($SUITE_LOCK)" 2
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
    suite_class="${GOH_GATE_CLASS:-work}"
    for a in "$@"; do
        [ -n "${GOH_GATE_CLASS:-}" ] || [ ! -e "${a%%::*}" ] || { suite_class=short; break; }
    done
    suite_wait="${GOH_SUITE_WAIT_MAX:-900}"
    case "$suite_wait" in
        '' | *[!0-9]*) die "GOH_SUITE_WAIT_MAX must be whole seconds (got '$suite_wait')" 2 ;;
    esac
    # shellcheck source=lib/gate_slot.sh
    . "$GOH/lib/gate_slot.sh"
    trap '[ "$(readlink "$SUITE_LOCK" 2>/dev/null)" = "$$" ] && rm -f "$SUITE_LOCK"; gate_slot_release' EXIT
    if [ -z "${GOH_GATE_SLOT:-}" ]; then
        gate_slot_acquire "goh suite ($PWD)" "$suite_class" "$suite_wait" \
            || die "the goh suite was not admitted within GOH_SUITE_WAIT_MAX=${suite_wait}s (holders above) -- nothing ran; rerun later, or raise GOH_SUITE_WAIT_MAX" 75
    fi
    # MEMORY ADMISSION (GOH.MEMADMIT): with the gate slots off (GOH_GATE_SLOTS=0) or inside a gate
    # found by ancestry, the suite still waits, bounded, while the machine is short of memory; a slot
    # taken above asked memory first.
    [ -n "${GOH_GATE_SLOT:-}" ] || "$GOH_PY" "$GOH/lib/mem_admit.py" wait --what "the goh suite"
    # THE DURATION TABLE (GOH.DURATIONSSTALE): a top-level run, and only one, is named goh's state
    # file (lib/fast_tests.py `cache()`, its one spelling), and tests/suite_home.py pops it, so no
    # worker or fixture run under this one writes it. A run nested on purpose is named none.
    GOH_TEST_DURATIONS="$(PYTHONPATH="$GOH" "$GOH_PY" -c 'from lib.fast_tests import cache; print(cache())')" \
        || die "cannot resolve the suite's duration table (lib/fast_tests.py cache())" 2
    export GOH_TEST_DURATIONS
else
    unset GOH_TEST_DURATIONS
fi
. "$GOH/gates/_pytest_root.sh"
# After the guards: a refused runner claims no root and cuts no other run's kept ones.
goh_pytest_root  # concurrent suites cannot prune each other's temp dirs; kept after a failure
export GATES_OF_HECK_SUITE_RUNNER=$$
# Refused by name, not as "No module named pytest" from the coverage gate's external export, the
# shape goh's first hosted run died of (CI.PUBLIC2): the manifest's goh-suite layer installs it.
"$GOH_PY" -m pytest --version >/dev/null 2>&1 \
    || die "pytest is not installed for the house interpreter $GOH_PY -- \"$GOH_PY\" -m pip install pytest"
# The suite's own imports (run 42930175911: twelve golden_core tests errored on a runner with no
# Pillow, and the numpy parity tier skipped): refused by name, installed by the goh-suite layer.
"$GOH_PY" -c 'import PIL' 2>/dev/null \
    || die "pillow is not installed for the house interpreter $GOH_PY -- \"$GOH_PY\" -m pip install pillow"
"$GOH_PY" -c 'import numpy' 2>/dev/null \
    || die "numpy is not installed for the house interpreter $GOH_PY -- \"$GOH_PY\" -m pip install numpy"
# The coverage gate's tests run coverage through the house interpreter (run 42950563400:
# test_gate_tests_unbound died on "'coverage' not installed").
"$GOH_PY" -c 'import coverage' 2>/dev/null \
    || die "coverage is not installed for the house interpreter $GOH_PY -- \"$GOH_PY\" -m pip install coverage"
# The tools the suite's FIXTURE repos are gated and shipped with (run 42944992082: ~45 Python-gate
# tests died on "ruff is not available through <python> -m", and 23 test_ship tests on a missing
# git-cliff): goh's own gates declare no python or ship layer, so the goh-suite layer installs them.
"$GOH_PY" -m ruff --version >/dev/null 2>&1 \
    || die "ruff is not installed for the house interpreter $GOH_PY -- \"$GOH_PY\" -m pip install ruff"
# One ruff (GOH.TOOLHYGIENE): `python3 -m ruff` was 0.17.0 while PATH's ruff was 0.17.4, so a gate's
# verdict hung on how it was invoked. Every invocation reports the same version, or nothing runs.
# ...and that version is goh's pin (gates/ruff_house.py RUFF, GOH.RUFFVERSION), the one CI installs.
"$GOH_PY" "$GOH/gates/ruff_agree.py" --pin "$("$GOH_PY" -S "$GOH/gates/ruff_house.py" --pin)" \
    || die "the rufs on this machine are not goh's pinned ruff (above) -- install it, then rerun"
command -v git-cliff >/dev/null 2>&1 || die "git-cliff is not on PATH (tools/ship.py's next version) -- brew install git-cliff"
# What goh's OWN pre-commit runs (test_self_hosted_repo_satisfies_its_own_hook; run 42957429139 died
# "swiftlint is not installed"): the shell lint, the swift twin and the rustfmt check.
command -v shellcheck >/dev/null 2>&1 || die "shellcheck is not on PATH (goh's own hook: shell lint) -- brew install shellcheck"
command -v swiftlint >/dev/null 2>&1 || die "swiftlint is not on PATH (goh's own hook: the swift twin) -- brew install swiftlint"
command -v rustfmt >/dev/null 2>&1 || die "rustfmt is not on PATH (goh's own hook: the rustfmt check) -- rustup component add rustfmt"
ARGS=(-q)
if "$GOH_PY" -c "import xdist" 2>/dev/null; then
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
    # shellcheck source=lib/worker_share.sh
    . "$GOH/lib/worker_share.sh"
    if [ -n "${GOH_SUITE_CORES:-}" ]; then
        cores="$GOH_SUITE_CORES"
    else
        goh_share_pool "$PYTEST_DEBUG_TEMPROOT/share-pool" 12 || die "cannot start the suite's live share (lib/worker_share.py pool)" 2
        cores="$GOH_SHARE_CEIL"
    fi
    case "$cores" in '' | *[!0-9]* | 0) die "cannot read this machine's core count (got '$cores')" 2 ;; esac
    [ "$cores" -gt 12 ] && cores=12
    ARGS+=(-n "$cores" --dist loadgroup)
else
    warn "pytest-xdist not installed — serial suite (pip3 install pytest-xdist)"
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
[ "$#" -ne 0 ] || goh_span_start
rc=0
"$GOH_PY" -m pytest "${TARGET[@]}" "${ARGS[@]}" "$@" || rc=$?
[ "$#" -ne 0 ] || goh_span_record "$suite_kind" goh "$([ "$rc" -eq 0 ] && echo 1 || echo 0)" --field "exit=$rc"
if [ "$rc" -eq 0 ]; then
    goh_rm_tree "$PYTEST_DEBUG_TEMPROOT"
else
    info "the failed run's temp root is kept: $PYTEST_DEBUG_TEMPROOT"
fi
(exit "$rc")
exit
} # parse-guard
