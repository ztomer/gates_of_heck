# shellcheck shell=bash
# Sourced. goh_pytest_root: a temp root of this run's own for every pytest it starts.
#
# pytest keeps the three newest <root>/pytest-of-<user>/pytest-N directories and deletes the older
# ones when ANY session starts, so two suites running at once on one machine pruned each other's temp
# dirs mid-run: 2277 tests errored on a vanished popen-gwN when two sessions landed into this repo at
# once (2026-10-10). PYTEST_DEBUG_TEMPROOT is pytest's own override of that root; exported, every
# pytest this shell starts prunes only inside its own. The caller removes it after a passing run with `goh_rm_tree`, NEVER a bare
# `rm -rf`: a test that leaves a read-only tree failed a gate with no `✗` line (2026-11-12).
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
# shellcheck source=lib/rm_tree.sh
. "${BASH_SOURCE[0]%/*}/../lib/rm_tree.sh"   # goh_rm_tree: the removal that copes with a read-only tree
#
# A failed run keeps its root, and a kept root held ~5 GiB: every test's tmp_path, ~100 MiB each
# where a test cloned this repo. Eleven of them were 55.3 GiB of TMPDIR (2026-11-12, GOH.PYTESTTMP).
# So the root is bounded twice: pytest keeps only a FAILED test's tmp_path (a passed test's goes at
# its teardown, pytest's own `tmp_path_retention_policy`), and before a run claims its root the
# older kept ones are cut to the newest GOH_PYTEST_KEEP (3), at most GOH_PYTEST_KEEP_BYTES (1 GiB)
# in all. A live run's root is never cut: its `.goh-owner` pid is alive (lib/prune_kept.py).
goh_pytest_root() {
    local tmp="${TMPDIR:-/tmp}"
    "${GOH_PY:-python3}" -S "${BASH_SOURCE[0]%/*}/../lib/prune_kept.py" "${tmp%/}" goh-pytest. \
        "${GOH_PYTEST_KEEP:-3}" 3600 --max-bytes "${GOH_PYTEST_KEEP_BYTES:-1073741824}" 2>/dev/null || true
    PYTEST_DEBUG_TEMPROOT="$(mktemp -d "${tmp%/}/goh-pytest.XXXXXX")"
    printf '%s\n' "$$" >"$PYTEST_DEBUG_TEMPROOT/.goh-owner"
    PYTEST_ADDOPTS="-o tmp_path_retention_policy=failed${PYTEST_ADDOPTS:+ $PYTEST_ADDOPTS}"
    export PYTEST_DEBUG_TEMPROOT PYTEST_ADDOPTS
}
