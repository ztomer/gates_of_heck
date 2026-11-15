# shellcheck shell=bash
# _self_gate.sh -- a commit IN a gates_of_heck checkout is judged by the tree it commits (GOH.SELFGATE).
#
# Every commit's gates come from the shared checkout's HEAD (C4, gates/_from_head.sh), and goh's own
# commits were no exception: a worktree commit that changed a gate's verdict over goh itself
# (RUFF.RULES, 686548f) was judged by the gate it replaced, and landed only with GOH_DIR=<worktree>
# GOH_LIVE=1. The first fix (756e6ae) handed such a commit to the worktree LIVE, and broke goh's
# own release: the version-bump commit was judged by a binary built for the tree before the bump
# and refused (reverted, d7dc7dc). So the subject is the STAGED TREE, exported whole like a HEAD
# export (`git write-tree` of the index being committed, keyed on that tree), and its binary is the
# build OF that tree: the export pins the tree (`.goh-head`), and gates/_goh_bin.sh answers with
# the store's build of its stamp, building it (`build-goh.sh --rev <tree>`) when no gate has yet.
# A goh checkout is a toplevel holding gates/commit_gate.sh, gates/_from_head.sh and crates/goh/;
# a consumer is never handed anywhere. GOH_SELF_GATED (<tree>|<toplevel>) marks the hand-off, so
# it happens once per commit and a fixture commit a test makes inside it is judged on its own.
# GOH_LIVE in the very checkout being committed is its developer's on purpose, and is kept.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"

goh_self_gate() { # <this commit_gate.sh> <args...> -- execs the staged tree's commit gate, or returns
    local top me tree
    top="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0
    [ -f "$top/gates/commit_gate.sh" ] && [ -f "$top/gates/_from_head.sh" ] \
        && [ -d "$top/crates/goh" ] || return 0
    top="$(cd "$top" && pwd -P)"
    me="$(cd "$(dirname "$1")/.." && pwd -P)"
    [ -n "${GOH_LIVE:-}" ] && [ "$me" = "$top" ] && return 0
    # The index being committed, read the one way (contract #12), in a subshell: this shell keeps
    # git's binding for the commit gate it hands to, whose own goh_hook_unbind carries it on.
    if ! tree="$( (goh_hook_unbind && goh_bind_hook_index && git write-tree) 2>/dev/null)" \
        || [ -z "$tree" ]; then
        echo "⚠ gates_of_heck: could not write this commit's tree -- judging it by $me's gates" >&2
        return 0
    fi
    case "${GOH_SELF_GATED:-}" in "$tree|$top") return 0 ;; esac
    _goh_export=""  # set by goh_export_rev (gates/_from_head.sh, sourced first)
    goh_export_rev "$top" "$tree" HEAD || return 0
    export GOH_SELF_GATED="$tree|$top" GOH_LIVE_ROOT="$top" GOH_DIR="$_goh_export"
    unset GOH_LIVE
    goh_path_front PYTHONPATH "$GOH_DIR"
    echo "· gates_of_heck: a goh commit -- judged by its own staged tree's gates (${tree:0:7})" >&2
    shift
    exec "$BASH" "$GOH_DIR/gates/commit_gate.sh" "$@"
}
