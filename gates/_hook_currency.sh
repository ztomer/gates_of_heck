# shellcheck shell=bash
# goh_stale_hooks: print each hook in the repo at $PWD that is a byte-identical RETIRED stock hook
# (retired_hooks.sha256, an entry not tagged `same-logic`) and that does NOT delegate, one name per
# line; nothing when current. ONE reading for both askers (GOH.SHIPITEM): structural.sh refuses such
# a hook at commit, and goh ship refuses it in preflight, before its first step -- koffee_big 2.17.5
# pushed and only the release's bump commit refused '.githooks/pre-commit is an OLDER stock hook'.
# goh_aging_hooks: the retired copies that DO delegate (GOH.HOOKSWEEP). Such a hook decides
# nothing: it execs gates/commit_gate.sh or push_gate.sh from the shared checkout, read at run time,
# so it runs today's gate, and refusing it ("the gate it runs is not today's") was false of it. It
# stranded every consumer at each hook-body change (0.107.0: five repos, each fixed by hand). It
# is named, with `goh hooks --refresh`, never refused.
# Judged by the hook's BYTES (its last exec), never by a tag someone must remember to add.
# GOH_ROOT names the gates checkout whose retired_hooks.sha256 is read. Sourced, never run.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
_goh_retired_hooks() {  # $1: 1 = the delegating copies, 0 = the ones carrying logic of their own
    local hooks h sum gate delegates
    hooks="$(git config core.hooksPath 2>/dev/null || echo .githooks)"
    for h in pre-commit pre-push; do
        [ -f "$hooks/$h" ] || continue
        sum="$(shasum -a 256 <"$hooks/$h" | cut -d' ' -f1)"
        awk -v s="$sum" -v h="$h" '$1 == s && $2 == h && !/same-logic/ { f = 1 }
                END { exit !f }' "$GOH_ROOT/retired_hooks.sha256" 2>/dev/null || continue
        gate="${h#pre-}_gate"
        # `exec bash "$commit_gate"` (18330c7 on) or `exec bash "$GOH/gates/push_gate.sh"` (0ac0f70)
        delegates=0
        grep -Eq "^exec bash \"\\\$(${gate}|GOH/gates/${gate}\\.sh)\"" "$hooks/$h" && delegates=1
        [ "$delegates" = "$1" ] && printf '%s/%s\n' "$hooks" "$h"
    done
    return 0
}
goh_stale_hooks() { _goh_retired_hooks 0; }
goh_aging_hooks() { _goh_retired_hooks 1; }
