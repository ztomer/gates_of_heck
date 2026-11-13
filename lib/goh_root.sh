# shellcheck shell=bash
# goh_root.sh -- goh_dir_repair: a GOH_DIR naming a REMOVED HEAD export is pointed back at a live
# gates_of_heck (GOH.TRANSIENTDIR).
#
# Every gate entry point re-runs from ~/.cache/goh/head/<sha> and exports GOH_DIR at it
# (gates/_from_head.sh), and the cache prunes old exports. A long-lived process started under a gate
# keeps the value for its whole life: Routines.app, `open`ed by a ship's release kit, ran its
# from_head jobs with it and every one exited 2 once the export was gone (2026-11-12 20:43 on; the
# disk-pressure reclaim never ran), and a Claude session carrying another had every commit refused
# by pre-commit. The export cannot be read once it is gone, so this falls back to GOH_LIVE_ROOT (the
# checkout that made it), then the default checkout, says so on stderr (the process is stale and
# should be restarted), and EXPORTS the repaired value, so whatever the caller starts agrees.
#
# ONE rule in three places: this function (copied VERBATIM into each stock hook in hooks/, which are
# installed by copy and can source nothing before they have found goh), lib/goh_root.py, and the
# native binary's `goh_root` (crates/goh/src/main.rs). tests/test_dead_goh_dir.py pins all three and
# the hooks' copies. A GOH_DIR that exists is never touched.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
goh_dir_repair() {
    [ -n "${GOH_DIR:-}" ] && [ ! -d "$GOH_DIR" ] || return 0
    local c
    # path-ok: house default, after GOH_DIR and GOH_LIVE_ROOT (the export itself is gone)
    for c in "${GOH_LIVE_ROOT:-}" "$HOME/Projects/gates_of_heck"; do
        if [ -n "$c" ] && [ -d "$c/gates" ]; then
            echo "⚠ gates_of_heck: GOH_DIR=$GOH_DIR no longer exists (a pruned HEAD export this" \
                "process inherited; restart it) -- using $c" >&2
            export GOH_DIR="$c"
            return 0
        fi
    done
    return 0
}
