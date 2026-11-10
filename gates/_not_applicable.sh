# shellcheck shell=bash
# gates/_not_applicable.sh -- local_ci.sh's reading of a step that exits 78, NOT APPLICABLE
# (lib/not_applicable.py, CI.PUBLIC2): a step that needs the owner's estate, run on a machine that
# has none. Sourced by local_ci.sh.
#
# lci_not_applicable <label> <cmd> <log>: 0 when the run may accept it -- a hosted run
# (GITHUB_ACTIONS=true) -- having said so on its own line with the step's reason; 1 anywhere else,
# having said why it is RED: off a hosted runner the owner's machine is the judge of record, and a
# step that did not run there has not passed (tests/test_not_applicable.py, both directions).
lci_not_applicable() {
    local label="$1" cmd="$2" log="$3" why
    why="$(grep -F -m 3 'NOT APPLICABLE' "$log" 2>/dev/null)"
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        warn "$label NOT APPLICABLE on this hosted runner (neither a pass nor a failure): $cmd"
        [ -z "$why" ] || printf '%s\n' "$why" | sed 's/^/    /' >&2
        return 0
    fi
    err "$label said NOT APPLICABLE (exit 78) off a hosted runner -- this machine is the judge"
    err "  of record and the step did not run, so it has not passed: $cmd"
    return 1
}
