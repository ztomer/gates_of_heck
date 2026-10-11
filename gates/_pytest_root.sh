# shellcheck shell=bash
# Sourced. goh_pytest_root: a temp root of this run's own for every pytest it starts.
#
# pytest keeps the three newest <root>/pytest-of-<user>/pytest-N directories and deletes the older
# ones when ANY session starts, so two suites running at once on one machine pruned each other's temp
# dirs mid-run: 2277 tests errored on a vanished popen-gwN when two sessions landed into this repo at
# once (2026-10-10). PYTEST_DEBUG_TEMPROOT is pytest's own override of that root; exported, every
# pytest this shell starts prunes only inside its own. The caller removes it after a passing run.
goh_pytest_root() {
    local tmp="${TMPDIR:-/tmp}"
    PYTEST_DEBUG_TEMPROOT="$(mktemp -d "${tmp%/}/goh-pytest.XXXXXX")"
    export PYTEST_DEBUG_TEMPROOT
}
