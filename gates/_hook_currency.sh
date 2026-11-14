# shellcheck shell=bash
# goh_stale_hooks: print each hook in the repo at $PWD that is a byte-identical RETIRED stock hook
# (retired_hooks.sha256, an entry not tagged `same-logic`), one name per line; nothing when current.
# ONE reading for both askers (GOH.SHIPITEM): structural.sh refuses such a hook at commit, and
# goh ship refuses it in preflight, before its first step -- koffee_big 2.17.5 pushed and only the
# release's bump commit refused '.githooks/pre-commit is an OLDER stock hook'. Sourced, never run.
# GOH_ROOT names the gates checkout whose retired_hooks.sha256 is read.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
goh_stale_hooks() {
    local hooks h sum
    hooks="$(git config core.hooksPath 2>/dev/null || echo .githooks)"
    for h in pre-commit pre-push; do
        [ -f "$hooks/$h" ] || continue
        sum="$(shasum -a 256 <"$hooks/$h" | cut -d' ' -f1)"
        if awk -v s="$sum" -v h="$h" '$1 == s && $2 == h && !/same-logic/ { f = 1 }
                END { exit !f }' "$GOH_ROOT/retired_hooks.sha256" 2>/dev/null; then
            printf '%s/%s\n' "$hooks" "$h"
        fi
    done
}
