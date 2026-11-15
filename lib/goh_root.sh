# shellcheck shell=bash
# goh_root.sh -- the ONE rule for GOH_LIVE_ROOT (GOH.LIVEROOTLEAK), and goh_dir_repair: a GOH_DIR
# naming a REMOVED HEAD export is pointed back at a live gates_of_heck (GOH.TRANSIENTDIR).
#
# GOH_LIVE_ROOT IS AN EXPORT'S. gates/_from_head.sh exports it beside the GOH_DIR it points at a
# HEAD export: the live checkout the export was cut from, which holds what an export lacks (bin/goh,
# history). It is exported, so it leaks into everything the export's gate starts, and a reader that
# took it from anywhere answered for a checkout nobody exported it for: the v0.119.0 ship's release
# worktree resolved main's goh through it (cee10b5), and a GOH_DIR naming a removed lane worktree
# was "repaired" to whatever root an ancestor's export had named. So every reader asks
# `goh_live_root_for DIR` -- GOH_LIVE_ROOT when DIR is an export, nothing otherwise -- and
# `goh_is_export DIR` is the one definition: DIR holds `.goh-head`, or, pruned, it sat in the head
# export cache (`goh_head_cache`, where _from_head.sh builds every export).
#
# The repair: every gate entry point re-runs from ~/.cache/goh/head/<sha> and exports GOH_DIR at it
# (gates/_from_head.sh), and the cache prunes old exports. A long-lived process started under a gate
# keeps the value for its whole life: Routines.app, `open`ed by a ship's release kit, ran its
# from_head jobs with it and every one exited 2 once the export was gone (2026-11-12 20:43 on; the
# disk-pressure reclaim never ran), and a Claude session carrying another had every commit refused
# by pre-commit. The export cannot be read once it is gone, so this falls back to its live root (the
# checkout that made it), then the default checkout, says so on stderr (the process is stale and
# should be restarted), and EXPORTS the repaired value, so whatever the caller starts agrees.
#
# ONE rule in three languages: this file, lib/goh_root.py, and crates/goh/src/goh_dir.rs. The binary
# resolver (gates/_goh_bin.sh), the export's bin/goh (gates/_export_bin_goh.sh) and gates/hook.sh
# (after the stock shim, which carries no logic) ask here. tests/test_live_root_rule.py runs one
# table through all three; tests/test_dead_goh_dir.py pins the repair. A GOH_DIR that exists is
# never touched.
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# goh_head_cache -- where gates/_from_head.sh builds every HEAD export, one directory per commit.
goh_head_cache() {
    printf '%s\n' "${GOH_HEAD_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/goh/head}"
}

# goh_is_export <dir> -- true when <dir> is a HEAD export, or was one and has been pruned.
goh_is_export() {
    [ -f "$1/.goh-head" ] && return 0
    [ ! -e "$1" ] && [ ! -L "$1" ] && [ "${1%/*}" = "$(goh_head_cache)" ]
}

# goh_live_root_for <dir> -- prints GOH_LIVE_ROOT when <dir> is an export, nothing otherwise.
goh_live_root_for() {
    if [ -n "${GOH_LIVE_ROOT:-}" ] && goh_is_export "$1"; then
        printf '%s\n' "$GOH_LIVE_ROOT"
    fi
}

# goh_dir_repaired -- where a GOH_DIR that is not a directory should point (its live root when it
# was an export, then the default checkout, whichever holds gates/); nothing when it is fine or
# nothing holds gates/.
goh_dir_repaired() {
    [ -n "${GOH_DIR:-}" ] && [ ! -d "$GOH_DIR" ] || return 0
    local c
    # path-ok: house default, after GOH_DIR and an export's GOH_LIVE_ROOT (the export itself is gone)
    for c in "$(goh_live_root_for "$GOH_DIR")" "$HOME/Projects/gates_of_heck"; do
        if [ -n "$c" ] && [ -d "$c/gates" ]; then
            printf '%s\n' "$c"
            return 0
        fi
    done
}

goh_dir_repair() {
    local c
    c="$(goh_dir_repaired)"
    [ -n "$c" ] || return 0
    echo "⚠ gates_of_heck: GOH_DIR=$GOH_DIR no longer exists (a pruned HEAD export this" \
        "process inherited; restart it) -- using $c" >&2
    export GOH_DIR="$c"
}
