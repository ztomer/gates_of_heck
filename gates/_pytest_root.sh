# shellcheck shell=bash
# Sourced. goh_pytest_root: a temp root of this run's own for every pytest it starts.
#
# pytest keeps the three newest <root>/pytest-of-<user>/pytest-N directories and deletes the older
# ones when ANY session starts, so two suites running at once on one machine pruned each other's temp
# dirs mid-run: 2277 tests errored on a vanished popen-gwN when two sessions landed into this repo at
# once (2026-10-10). PYTEST_DEBUG_TEMPROOT is pytest's own override of that root; exported, every
# pytest this shell starts prunes only inside its own. The caller removes it after a passing run with `goh_rm_tree`, NEVER a bare
# `rm -rf`: a test that leaves a read-only tree failed a gate with no `✗` line (2026-11-12).
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
#
# Sourced by every script that starts pytest (`tools/pytest.sh`, `py_gate.sh`, `coverage_gate.sh`),
# never run: `goh_pytest_root` exports `PYTEST_DEBUG_TEMPROOT` to a temp root of the run's own,
# because pytest prunes all but the three newest `pytest-of-<user>/pytest-N` when ANY session
# starts, and two concurrent suites deleted each other's temp dirs mid-run (2277 errors,
# 2026-10-10). The root is bounded: it records its owner's pid in `.goh-owner`, pytest keeps only a
# FAILED test's `tmp_path` (`-o tmp_path_retention_policy=failed` in `PYTEST_ADDOPTS`), and older
# kept roots are cut to the newest `GOH_PYTEST_KEEP` (3) within `GOH_PYTEST_KEEP_BYTES` (1 GiB)
# first (GOH.PYTESTTMP: 55.3 GiB of kept roots). Invoked by sourced.
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_pytest_root.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/rm_tree.sh
. "${BASH_SOURCE[0]%/*}/../lib/rm_tree.sh"   # goh_rm_tree: the removal that copes with a read-only tree
. "${BASH_SOURCE[0]%/*}/../lib/tmp_root.sh"  # goh_tmp: $TMPDIR/goh.noindex, which Spotlight never indexes
#
# A failed run keeps its root, and a kept root held ~5 GiB: every test's tmp_path, ~100 MiB each
# where a test cloned this repo. Eleven of them were 55.3 GiB of TMPDIR (2026-11-12, GOH.PYTESTTMP).
# So the root is bounded twice: pytest keeps only a FAILED test's tmp_path (a passed test's goes at
# its teardown, pytest's own `tmp_path_retention_policy`), and before a run claims its root the
# older kept ones are cut to the newest GOH_PYTEST_KEEP (3), at most GOH_PYTEST_KEEP_BYTES (1 GiB)
# in all. A live run's root is never cut: its `.goh-owner` pid is alive (lib/prune_kept.py).
goh_pytest_root() {
    local tmp; tmp="$(goh_tmp)" || return 1   # 94% of what mds walked in gates (GOH.TMPNOINDEX)
    "${GOH_PY:-python3}" -S "${BASH_SOURCE[0]%/*}/../lib/prune_kept.py" "$tmp" goh-pytest. \
        "${GOH_PYTEST_KEEP:-3}" 3600 --max-bytes "${GOH_PYTEST_KEEP_BYTES:-1073741824}" 2>/dev/null || true
    # A SLOT, NOT A FRESH NAME (GOH.FSCHURN, 2026-11-18): the lowest `goh-pytest.slotNN` that does
    # not exist, made by an atomic mkdir, so a passing run's root (removed by its caller) is the next
    # run's, and every tmp_path below it -- pytest numbers them from 0 under a fresh root -- is the
    # same path run after run. A random name per run made every path a test wrote one fseventsd had
    # never seen; it sat at one core and 11.7 GB with goh 78-99% of the events on /. A live run's
    # root and a red run's kept one hold their slots (prune_kept bounds the kept); past the last
    # of the 32 slots, a fresh name as before. Pinned by tests/test_reused_temp_names.py.
    local n=0
    PYTEST_DEBUG_TEMPROOT=""
    while [ "$n" -lt 32 ]; do
        if mkdir "$tmp/goh-pytest.slot$(printf %02d "$n")" 2>/dev/null; then
            PYTEST_DEBUG_TEMPROOT="$tmp/goh-pytest.slot$(printf %02d "$n")"
            break
        fi
        n=$((n + 1))
    done
    [ -n "$PYTEST_DEBUG_TEMPROOT" ] || PYTEST_DEBUG_TEMPROOT="$(mktemp -d "$tmp/goh-pytest.XXXXXX")"
    # `<pid> <start time>` (GOH.STRAYSIGNAL2): lib/prune_kept.py keeps the root only while THIS run lives.
    printf '%s %s\n' "$$" "$(ps -o lstart= -p "$$" 2>/dev/null)" >"$PYTEST_DEBUG_TEMPROOT/.goh-owner"
    PYTEST_ADDOPTS="-o tmp_path_retention_policy=failed${PYTEST_ADDOPTS:+ $PYTEST_ADDOPTS}"
    export PYTEST_DEBUG_TEMPROOT PYTEST_ADDOPTS
}
