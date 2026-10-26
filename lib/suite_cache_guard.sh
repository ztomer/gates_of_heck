# shellcheck shell=bash
# suite_cache_guard.sh -- sourced, never run. goh's own suite never writes the user's caches (G11/G12).
#
# THE CLASS. A test ran a gate whose cache root fell back to the REAL ~/.cache/goh: push_gate left a
# zero-byte log there at every suite run (G11), and the head export cache, the shared goh binary
# store and agent_guard's spawn ledger (G12) default there too. tests/suite_home.py points HOME and
# XDG_CACHE_HOME at a suite-owned home and names the real one in GATES_OF_HECK_SUITE_REAL_HOME (not
# GOH_-prefixed, so no env scrub drops it); every such writer asks this before it writes, and under
# the suite a path in the user's ~/.cache/goh -- outside the suite's own ~/.cache/goh/suite -- is
# refused, naming the writer and the test. Outside goh's suite this is always 0.

# goh_suite_cache_ok <path> <writer> -- 0 when <path> may be written; 1, with the reason on stderr.
goh_suite_cache_ok() {
    local real="${GATES_OF_HECK_SUITE_REAL_HOME:-}"
    [ -n "$real" ] || return 0   # set by tests/suite_home.py alone: keyed on nothing a scrub drops
    case "$1/" in
        "$real/.cache/goh/suite/"*) return 0 ;;
        "$real/.cache/goh/"*)
            echo "✗ gates_of_heck: $2 under goh's suite (${PYTEST_CURRENT_TEST:-a test}) would write the user's cache: $1 -- the suite's HOME/XDG_CACHE_HOME (tests/suite_home.py) must reach it" >&2
            return 1 ;;
    esac
    return 0
}
