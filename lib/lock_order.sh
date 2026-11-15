# shellcheck shell=bash
# lock_order.sh -- the shell's half of lib/lock_order.py: take a goh lock only in the table's order.
#
# HOUSE LIB, sourced (GOH.LOCKORDER). The rule, the incident and the table are lib/lock_order.py's
# and lib/lock_order.tsv's; this file is the shell's spelling of the same three calls, each one
# running that module so there is ONE implementation of the order:
#
#   goh_lock_check NAME [WHERE]  0 when waiting on NAME keeps the order; else 78, the refusal printed
#   goh_lock_take NAME FD [--nb] [--where P]
#                                the kernel flock on the open FD for NAME: --nb a try-lock (1 when it
#                                is taken), else checked first and waited on; taken, NAME@$$:KEY joins
#                                GOH_HELD_LOCKS, exported, so every child knows what is held
#   goh_lock_mark NAME [WHERE]   NAME@$$:KEY joins GOH_HELD_LOCKS (a lock taken some other way: mkdir)
#   goh_lock_drop NAME           NAME@$$ leaves it: every release before exit calls it, or a child
#                                started later is refused a lock nobody holds (Adv360's probe)
# WHERE (a path in the lock's repository; the working directory when absent) scopes a `repo` lock
# and the mark's KEY (lib/lock_order.py's "SCOPE", GOH.LOCKNEST).
#
# The flock is held by the open file description behind FD, which this shell keeps: it ends when
# the shell closes FD or exits, SIGKILL included. Pinned by tests/test_lock_order.py.
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/house_python.sh" || return 1
_goh_lock_order_lib="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
_goh_lock_order() { # the module's main() beside this file, in this process tree (fds and all)
    "$GOH_PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import lock_order
sys.exit(lock_order.main(sys.argv[2:]))' "$_goh_lock_order_lib" "$@" </dev/null
}

goh_lock_check() {
    _goh_lock_order check "${1:?lock name}" ${2:+--where "$2"}
}

goh_lock_mark() {
    GOH_HELD_LOCKS="$(_goh_lock_order held "${1:?lock name}" "$$" ${2:+--where "$2"})" || return
    export GOH_HELD_LOCKS
}

goh_lock_drop() {
    local want="${1:?lock name}@$$" part kept=""
    local IFS=,
    for part in ${GOH_HELD_LOCKS:-}; do
        [ "${part%%:*}" = "$want" ] || kept="${kept:+$kept,}$part"
    done
    GOH_HELD_LOCKS="$kept"
    export GOH_HELD_LOCKS
}

goh_lock_take() {
    local name="${1:?lock name}" fd="${2:?fd}" where="" arg
    local -a _goh_lock_flags=()
    shift 2
    while [ "$#" -gt 0 ]; do
        arg="$1"
        shift
        if [ "$arg" = "--where" ]; then
            where="${1:?--where needs a path}"
            shift
        else
            _goh_lock_flags+=("$arg")
        fi
    done
    _goh_lock_order flock "$fd" "$name" ${_goh_lock_flags[@]+"${_goh_lock_flags[@]}"} \
        ${where:+--where "$where"} || return
    goh_lock_mark "$name" "$where"
}
