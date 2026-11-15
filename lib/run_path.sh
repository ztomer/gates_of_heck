# shellcheck shell=bash
# run_path.sh -- sourced, never run. The ONE place goh's temp, lock and staging paths come from,
# rooted OUTSIDE every work tree goh serves (GOH.CLASS.CHECKOUT part 2).
#
# The class: goh wrote its own scratch INTO the checkouts it serves -- `bin/.build.lock`,
# `bin/goh.<pid>.staging`, the `build/release-vX` worktree, `<repo>/.goh-tree.lock` -- and a
# `git write-tree` on the default index took `index.lock` in a shared checkout while a commit there
# held it (GOH.REFRESHINDEXLOCK). Each read as "N uncommitted" on a claim card, failed a ship that
# verified the live tree, or refused another writer. So every such path is minted here:
#
#   goh_run_root              $GOH_RUN_DIR, else $XDG_STATE_HOME/goh/run, else ~/.local/state/goh/run
#   goh_run_dir TREE          <root>/<basename>-<sha256(physical TREE)[:16]>, created; one per tree,
#                             so two worktrees of one repo never share a lock and one tree always
#                             gets the same directory back (a stable path is what keeps a cargo
#                             build-dir, keyed by workspace path, incremental)
#   goh_index_tree [DIR]      the tree of the index git would commit (the hook's GIT_INDEX_FILE, or the
#                             one a hook carried, `goh_bind_hook_index`), written from a PRIVATE copy under the run root, so it
#                             never takes, and never waits on, the shared `index.lock`
#
# The Python face is lib/run_path.py and the Rust face crates/goh/src/run_path.rs: one root, one key.
# A new raw temp, lock or staging path under a work tree is refused by `goh run-paths`
# (crates/goh/src/runpaths.rs). tests/test_run_path.py, tests/test_entry_points_leave_no_strays.py.

_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

goh_run_root() {
    local r="${GOH_RUN_DIR:-}"
    if [ -z "$r" ]; then
        r="${XDG_STATE_HOME:-}"
        case "$r" in /*) ;; *) r="${HOME:?HOME is unset}/.local/state" ;; esac
        r="$r/goh/run"
    fi
    case "$r" in
        /*) printf '%s\n' "$r" ;;
        *) printf '%s\n' "✗ GOH_RUN_DIR must be an absolute path: '$r'" >&2; return 2 ;;
    esac
}

# _goh_run_sha256 -- hex SHA-256 of stdin, by whichever tool exists (lib/run_path.py: hashlib).
_goh_run_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | cut -d' ' -f1
    else
        shasum -a 256 | cut -d' ' -f1
    fi
}

goh_run_dir() {
    local tree="${1:?goh_run_dir needs a tree}" phys root hex d
    phys="$(cd "$tree" 2>/dev/null && pwd -P)" || {
        printf '%s\n' "✗ goh_run_dir: no such directory: $tree" >&2; return 2; }
    root="$(goh_run_root)" || return
    hex="$(printf '%s' "$phys" | _goh_run_sha256)" || return 1
    d="$root/${phys##*/}-${hex:0:16}"
    mkdir -p "$d" || return 1
    printf '%s\n' "$d"
}

goh_index_tree() {
    (
        [ -z "${1:-}" ] || cd "$1" || exit 2
        local idx root copy tree
        # A hook drops its git variables at entry and carries a `commit -a` / `commit -- path`
        # index only as GOH_HOOK_INDEX_FILE: the helper binds it itself, so a caller that forgot
        # does not copy (and name the tree of) the repository's own, pre-commit, index.
        # A checkout holding only lib/ (a placement test's fixture) has no gates/: nothing is carried.
        command -v goh_bind_hook_index >/dev/null 2>&1 || [ ! -f "$_goh_d/../gates/_git_env.sh" ] \
            || . "$_goh_d/../gates/_git_env.sh" || exit 1
        ! command -v goh_bind_hook_index >/dev/null 2>&1 || goh_bind_hook_index
        idx="${GIT_INDEX_FILE:-}"
        if [ -n "$idx" ]; then
            case "$idx" in /*) ;; *) idx="$PWD/$idx" ;; esac  # relative to the cwd, as git reads it
        else
            idx="$(git rev-parse --path-format=absolute --git-path index 2>/dev/null)" || exit 1
        fi
        root="$(goh_run_root)" || exit 2
        mkdir -p "$root/index" || exit 1
        copy="$(mktemp "$root/index/tree.XXXXXX")" || exit 1
        trap 'rm -f "$copy"' EXIT
        # No index yet is an empty one; an index renamed in under us is read whole (rename is atomic).
        if [ -f "$idx" ]; then cp "$idx" "$copy" || exit 1; else rm -f "$copy"; fi
        tree="$(GIT_INDEX_FILE="$copy" git write-tree)" || exit 1
        printf '%s\n' "$tree"
    )
}
