# shellcheck shell=bash
# Sourced. goh_pytest_root: a temp root of this run's own for every pytest it starts.
#
# pytest keeps the three newest <root>/pytest-of-<user>/pytest-N directories and deletes the older
# ones when ANY session starts, so two suites running at once on one machine pruned each other's temp
# dirs mid-run: 2277 tests errored on a vanished popen-gwN when two sessions landed into this repo at
# once (2026-10-10). PYTEST_DEBUG_TEMPROOT is pytest's own override of that root; exported, every
# pytest this shell starts prunes only inside its own. The caller removes it after a passing run with `goh_rm_tree`, NEVER a bare
# `rm -rf`: a test that leaves a read-only tree failed a gate with no `✗` line (2026-11-12).
# shellcheck source=lib/rm_tree.sh
. "${BASH_SOURCE[0]%/*}/../lib/rm_tree.sh"   # goh_rm_tree: the removal that copes with a read-only tree
goh_pytest_root() {
    local tmp="${TMPDIR:-/tmp}"
    PYTEST_DEBUG_TEMPROOT="$(mktemp -d "${tmp%/}/goh-pytest.XXXXXX")"
    export PYTEST_DEBUG_TEMPROOT
}
