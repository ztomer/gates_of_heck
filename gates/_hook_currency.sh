# shellcheck shell=bash
# goh_stale_hooks: print each hook in the repo at $PWD that is a STALE stock copy carrying gate logic
# of its own, one `.githooks/<name>` per line; nothing when current. structural.sh refuses such a
# hook at commit (koffee_big 2.17.5: only the release's bump commit refused it, GOH.SHIPITEM).
# goh_aging_hooks: the stale copies that DELEGATE to the shared checkout's gates, read at run time,
# so they run today's gate: named with `goh hooks --refresh`, never refused (GOH.HOOKSWEEP).
# Both are lib/hook_currency.py's one reading, the same one `goh hooks`, the estate audit and
# goh ship ask (GOH.HOOKCURRENCYDUP: this file was a second, narrower rule). GOH_ROOT names the
# gates checkout whose retired_hooks.sha256 and hooks/ are read. Sourced, never run.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/house_python.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib/house_python.sh" || return 1
_goh_hook_currency() {  # $1: --refused | --aging
    "$GOH_PY" -S "$(dirname "${BASH_SOURCE[0]}")/../lib/hook_currency.py" "$1" --goh "$GOH_ROOT"
}
goh_stale_hooks() { _goh_hook_currency --refused; }
goh_aging_hooks() { _goh_hook_currency --aging; }
