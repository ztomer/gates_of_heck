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
#     goh_share_pool <dir> <ceiling>
#         # export GOH_SHARE_POOL/CEIL/OWN: a run started at its ceiling whose workers take a token
#         # per test from the LIVE share (GOH.SHAREGROW; tools/pytest.sh, tests/_share_tokens.py)
#     goh_workers
#         # print this gate's share: the flag for a runner that reads no variable (swift, xcodebuild, make)

# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/worker_share.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
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

goh_share_pool() { # goh_share_pool <dir> <ceiling> -- export a live-share token pool for one run
    local line out
    out="$("$GOH_PY" -S "$WORKER_SHARE_HELPER" pool --path "$1" --ceiling "$2")" || return 1
    while IFS= read -r line; do
        [ -n "$line" ] && export "${line?}"
    done <<<"$out"
}

goh_workers() {
    if [ -n "${GOH_GATE_WORKERS:-}" ]; then
        printf '%s\n' "$GOH_GATE_WORKERS"
    else
        "$GOH_PY" -S "$WORKER_SHARE_HELPER" share
    fi
}
