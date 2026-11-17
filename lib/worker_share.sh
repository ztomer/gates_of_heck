# shellcheck shell=bash
# worker_share.sh -- the shell's half of the worker share (GOH.POOLSHARE; the rule and the incident
# are lib/worker_share.py's header). Sourced by lib/gate_slot.sh and by every goh layer that starts
# a runner (gates/rust_gate.sh, py_gate.sh, swift_gate.sh, coverage_gate.sh):
#
#     goh_workers_export [--split N] [--admitted]
#         # export the runner variables (GOH_GATE_WORKERS, CARGO_BUILD_JOBS, RUST_TEST_THREADS,
#         # PYTEST_XDIST_AUTO_NUM_WORKERS, ...) for this gate's share; --split N gives
#         # one of N steps run at once its part; --admitted counts the slots afresh (admission)
#     goh_workers_ensure
#         # a layer's entry: export them unless the admission already did (a layer under a consumer
#         # gate.sh whose slot structural.sh took FOR it inherits nothing, so it counts)
#     goh_workers
#         # print this gate's share: the flag for a runner that reads no variable (swift, xcodebuild, make)

_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/house_python.sh" || exit 1   # GOH_PY: the house interpreter (G2d)
WORKER_SHARE_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/worker_share.py"

goh_workers_export() {
    local line out
    out="$("$GOH_PY" -S "$WORKER_SHARE_HELPER" env "$@")" || return 1
    while IFS= read -r line; do
        [ -n "$line" ] && export "${line?}"
    done <<<"$out"
}

goh_workers_ensure() {
    [ -n "${GOH_GATE_WORKERS:-}" ] || goh_workers_export --split 1   # counted: one gate, unsplit
}

goh_workers() {
    if [ -n "${GOH_GATE_WORKERS:-}" ]; then
        printf '%s\n' "$GOH_GATE_WORKERS"
    else
        "$GOH_PY" -S "$WORKER_SHARE_HELPER" share
    fi
}
