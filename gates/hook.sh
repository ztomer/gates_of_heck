#!/usr/bin/env bash
# gates/hook.sh HOOK-PATH [ARG...] -- what every stock hook does, read at run time (GOH.CLASS.ROLLOUT).
#
# WHY THE HOOK HOLDS NOTHING. install.sh COPIES hooks into each consumer's tracked `.githooks`, so
# whatever logic a copy carried reached a repo only when it was refreshed: 33 repos ran stale stock
# hooks on 2026-11-15, and a refresh is a commit into a tree another agent may be working in. The
# stock hook is now a shim (hooks/pre-commit; the same bytes under every name) that only finds this
# checkout and runs this file, so every line that used to be copied -- the bash 5 guard, the dead
# GOH_DIR fallback (GOH.TRANSIENTDIR), the hook-to-gate dispatch, the git unbind -- lives here and
# is current the moment goh is. tests/test_hook_shim.py: a change HERE reaches a consumer commit
# with no refresh.
#
# The shim tried GOH_DIR (or GOH), GOH_LIVE_ROOT, the house default, then the repo itself, and ran
# the first that has this file. When that is not the GOH_DIR it was handed -- a pruned HEAD export
# a long-lived process inherited, or a goh older than this file -- it is said once on stderr (the
# process is stale; restart it) and GOH_DIR is EXPORTED at this checkout, so every gate the hook
# starts agrees with the one that judged it. Like every gate entry point it then re-runs from HEAD's
# export (gates/_from_head.sh, C4), so an uncommitted edit here judges nobody.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail
# shellcheck source=tui/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../tui/lib.sh"
# Said by the live copy, before the trampoline re-runs this from HEAD's export with GOH_DIR at it.
if [ -n "${GOH_DIR:-}" ] && [ ! -f "$GOH_DIR/gates/hook.sh" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [ -d "$GOH_DIR" ]; then
        warn "gates_of_heck: GOH_DIR=$GOH_DIR predates gates/hook.sh -- using $here"
    else
        warn "gates_of_heck: GOH_DIR=$GOH_DIR no longer exists (a pruned HEAD export this process inherited; restart it) -- using $here"
    fi
    export GOH_DIR="$here"
fi
# shellcheck source=gates/_from_head.sh
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
goh="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GOH_DIR="$goh"

hook="${1:-}"
hook="${hook##*/}"
[ $# -eq 0 ] || shift

case "$hook" in
    pre-commit)
        # The repo's gate in STAGED scope, and structural even when that gate leaves it out.
        exec bash "$goh/gates/commit_gate.sh" "$@" ;;
    pre-push)
        # "$@" carries the remote's name: push_gate.sh skips commits the remote already has.
        exec bash "$goh/gates/push_gate.sh" "$@" ;;
    commit-msg)
        exec bash "$goh/gates/commit_msg.sh" "$@" ;;
    post-rewrite|pre-applypatch)
        # git binds the hook to its repository; drop that before anything is spawned (contract #12).
        # shellcheck source=gates/_git_env.sh
        . "$goh/gates/_git_env.sh"
        goh_hook_unbind || exit $?
        exec bash "$goh/gates/sequencer_gate.sh" "$hook" "$@" ;;
    *)
        err "gates/hook.sh: no stock hook is named '${hook:-<none>}' (run as: hook.sh HOOK-PATH [ARG...])"
        exit 2 ;;
esac
exit
} # parse-guard
