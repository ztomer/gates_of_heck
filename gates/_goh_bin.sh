# shellcheck shell=bash
# _goh_bin.sh — sourced, never run. The ONE resolution of the native `goh` binary.
#
# It was written three times (structural.sh, and twice in rust_gate.sh for lints and no-allow),
# and the copies had drifted by the time they were compared (2026-09-23): the lints copy, given a
# GOH_BIN that was set but not executable, fell through to bin/goh — a different binary than the
# one named — where the other two reported it and ran Python. Every caller now asks here.
#
# Order: GOH_NO_NATIVE forces Python; an explicit GOH_BIN is trusted or REPORTED, never silently
# replaced; then the build of THIS gate's commit from the store (below); then `goh` on PATH.
#
# THE BINARY ANSWERS TO THE SAME COMMIT AS THE SOURCE. A gate run from an export of HEAD (C4) is
# pinned to that commit for its whole run; `bin/goh` follows the live checkout's HEAD (C3). They
# agree until goh lands mid-run: on 2026-10-17 v0.29.0 landed while divoom-control's and monitor's
# pushes were mid-suite, the resolver rebuilt bin/goh for the new HEAD, and the next nested gate
# judged its export's 0.28.1 manifest against the live 0.29.0 binary and refused the push. So every
# build also lands in a store keyed by its source stamp, `bin/by-stamp/<sha1 of the stamp>/goh`, and
# the answer is ALWAYS the store's copy for the pinned commit: immutable, so a land rewriting
# bin/goh cannot change what a run (or a child it handed its answer to) executes. A pin no gate has
# built yet is built (`build-goh.sh --rev`). tests/test_binary_pinned_to_export.py.
#
# Sets `goh_native` (the binary, or empty) and `goh_native_why` (why it is empty, for the one line
# the caller prints; empty when GOH_NO_NATIVE asked for Python on purpose).

# Its git on the gates checkout drops a hook's repository variables (contract #12); sourced here
# too, because a probe sources this file alone (tests/_drift_guard.py).
# shellcheck source=gates/_git_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/_git_env.sh"
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/../lib/house_python.sh" || exit 1   # GOH_PY: the house interpreter, never PATH's first python3 (G2d)

goh_resolve_native() {
    goh_native=""
    goh_native_why=""
    if [ -n "${GOH_NO_NATIVE:-}" ]; then
        # Retired with the Python tier (Phase N3): there is nothing else to run. Said, not obeyed,
        # so a CI step still setting it keeps working -- on the one tier that exists.
        echo "· GOH_NO_NATIVE is retired: the native binary is the only tier -- ignoring it" >&2
    fi
    if [ -n "${GOH_BIN:-}" ]; then
        if [ -x "${GOH_BIN}" ]; then
            goh_native="$GOH_BIN"
        else
            goh_native_why="GOH_BIN=$GOH_BIN is not an executable"
        fi
        return 0
    fi
    local here want key pin
    # bin/goh is not in git: run from an export of HEAD (C4), the binary comes from the LIVE
    # checkout's bin/, for the commit the export pins (`.goh-head`).
    here="${GOH_LIVE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}/gates"
    pin="$(cat "$(dirname "${BASH_SOURCE[0]}")/../.goh-head" 2>/dev/null || true)"
    # ONCE PER PROCESS TREE: the answer below costs ~12 spawns (the GOH_LIVE delta, HEAD's stamp,
    # the binary's own), and every goh.sh call paid it again (2026-10-06,
    # tests/test_goh_bin_once.py). A parent's answer for the same gates root and GOH_LIVE mode is
    # taken while its binary is still executable; anything else is asked again.
    key="$(cd "$here/.." && pwd -P)|live:${GOH_LIVE:-}${pin:+|pin:$pin}"
    if [ "${GOH_RESOLVED_FOR:-}" = "$key" ] && [ -x "${GOH_RESOLVED_BIN:-}" ]; then
        goh_native="$GOH_RESOLVED_BIN"
        return 0
    fi
    _goh_resolve_uncached "$here" "$pin"
    if [ -n "$goh_native" ]; then
        export GOH_RESOLVED_FOR="$key" GOH_RESOLVED_BIN="$goh_native"
    fi
}

_goh_resolve_uncached() { # <gates dir> [<pinned commit>]
    local here="$1" pin="${2:-}" head want stored
    # GOH_LIVE runs the working tree on purpose -- so the binary is the working tree's too.
    # bin/goh is HEAD's (C3), and under GOH_LIVE with uncommitted Rust it answered for code the
    # run was not testing: a new subcommand read "unrecognized", and a test of an uncommitted fix
    # could pass against old code (found 2026-10-06 porting dep currency, Phase N1).
    goh_live_binary "$here/.." && return 0
    head="$( (goh_unbind_git; git -C "$here/.." rev-parse -q --verify HEAD) 2>/dev/null || true)"
    [ -n "$pin" ] || pin="$head"
    want="$(goh_stamp_at "$here/.." "$pin")"
    if [ -z "$want" ]; then
        # Not a git checkout (a tarball install): nothing to compare, bin/goh is what there is.
        if [ -x "$here/../bin/goh" ]; then
            goh_native="$here/../bin/goh"
        elif command -v goh >/dev/null 2>&1; then
            goh_native="$(command -v goh)"
        else
            goh_native_why="goh binary not built (cd $here/.. && ./install.sh builds it)"
        fi
        return 0
    fi
    stored="$(goh_store_path "$here/.." "$want")"
    if [ "$(goh_stamp_of "$stored")" != "$want" ]; then
        if [ "$pin" = "$head" ]; then
            # bin/goh is OURS to rebuild: built from other source than HEAD's (or before stamps
            # existed), it is rebuilt from HEAD here, under build-goh.sh's lock. A version check
            # alone passed a same-version binary that lacked a step (2026-10-05, BACKLOG C3).
            if [ "$(goh_stamp_of "$here/../bin/goh")" != "$want" ]; then
                echo "· bin/goh was not built from HEAD's source -- rebuilding it (scripts/build-goh.sh)" >&2
                bash "$here/../scripts/build-goh.sh" --if-stale >&2 \
                    || echo "✗ the rebuild failed (above)" >&2
            fi
            [ "$(goh_stamp_of "$here/../bin/goh")" = "$want" ] \
                && goh_store_adopt "$here/../bin/goh" "$stored"
        else
            echo "· goh moved on since this gate pinned ${pin:0:7} -- building that commit's goh" >&2
            bash "$here/../scripts/build-goh.sh" --rev "$pin" >&2 \
                || echo "✗ the build of ${pin:0:7} failed (above)" >&2
        fi
    fi
    if [ "$(goh_stamp_of "$stored")" = "$want" ]; then
        : > "$(dirname "$stored")/.goh-used" 2>/dev/null || true
        goh_native="$stored"
    elif command -v goh >/dev/null 2>&1 && [ "$(goh_stamp_of "$(command -v goh)")" = "$want" ]; then
        goh_native="$(command -v goh)"
    elif [ -x "$here/../bin/goh" ]; then
        goh_native_why="bin/goh is not built from ${pin:0:7}'s source and could not be rebuilt"
    else
        goh_native_why="goh binary not built (cd $here/.. && ./install.sh builds it)"
    fi
}

# goh_store_path <goh root> <stamp> -- where the build of <stamp> lives (scripts/build-goh.sh
# writes it, the resolver reads it): `bin/by-stamp/<sha1 of the stamp>/goh`.
goh_store_path() {
    printf '%s/bin/by-stamp/%s/goh' "$1" "$(printf %s "$2" | shasum | cut -c1-40)"
}

# goh_store_adopt <binary> <store path> -- copy a current bin/goh into the store, whole: staged
# beside it and renamed, so a reader never sees half a binary. Prunes the store when it adds one.
goh_store_adopt() {
    local dir
    dir="$(dirname "$2")"
    mkdir -p "$dir" && cp "$1" "$dir/.goh.$$" && mv "$dir/.goh.$$" "$2" || return 0
    : > "$dir/.goh-used"
    goh_store_prune "$(dirname "$dir")" "$(basename "$dir")"
}

# goh_store_prune <store> <current key> -- the export cache's policy (gates/_head_cache.py), with a
# smaller keep: a binary is ~8 MB, and one used within the hour is never removed.
goh_store_prune() {
    "$GOH_PY" -S -c 'import sys; sys.path.insert(0, sys.argv[1]); import _head_cache; _head_cache.prune(sys.argv[2], sys.argv[3], keep=4)' \
        "$(dirname "${BASH_SOURCE[0]}")" "$1" "$2" 2>/dev/null || true
}

# The working tree's Rust inputs as they differ from HEAD: a hash of the diff and of every
# untracked file under crates/, or nothing when the tree's Rust IS HEAD's.
goh_live_delta() {
    local root="$1" diff untracked
    diff="$( (goh_unbind_git
              git -C "$root" diff HEAD -- crates Cargo.toml Cargo.lock rust-toolchain.toml) 2>/dev/null)"
    untracked="$( (goh_unbind_git
                   cd "$root" && git ls-files -z --others --exclude-standard -- crates \
                   | xargs -0 -r shasum 2>/dev/null) 2>/dev/null)"
    [ -z "$diff$untracked" ] && return 0
    printf '%s\n%s' "$diff" "$untracked" | shasum | cut -d' ' -f1
}

# Under GOH_LIVE with the tree's Rust ahead of HEAD: build THAT goh (once per delta) and use it.
# Returns 1 when it does not apply (no GOH_LIVE, or the tree's Rust is HEAD's).
goh_live_binary() {
    [ -n "${GOH_LIVE:-}" ] || return 1
    local root="$1" delta bin
    delta="$(goh_live_delta "$root")"
    [ -n "$delta" ] || return 1
    local target="${GOH_LIVE_TARGET_DIR:-$root/target/goh-live}"
    bin="$target/release/goh"
    if [ ! -x "$bin" ] || [ "$(cat "$bin.delta" 2>/dev/null)" != "$delta" ]; then
        echo "· GOH_LIVE: the working tree's Rust differs from HEAD -- building its goh" >&2
        if (cd "$root" && cargo build -q --locked --release -p goh --target-dir "$target") >&2; then
            printf '%s' "$delta" > "$bin.delta"
        fi
    fi
    if [ -x "$bin" ] && [ "$(cat "$bin.delta" 2>/dev/null)" = "$delta" ]; then
        goh_native="$bin"
    else
        goh_native_why="GOH_LIVE: the working tree's goh could not be built"
    fi
    return 0
}

# goh_stamp_at <goh root> <commit> -- the git trees of the binary's build inputs at <commit>, as
# crates/goh/build.rs stamps them; empty when the root is not a git checkout (nothing to compare).
goh_stamp_at() {
    # Captured FIRST, then joined: a revision git cannot resolve (no HEAD yet, a missing input) is
    # ECHOED to stdout, and under pipefail a failing git inside a pipe killed the gate silently.
    local out c="$2"
    [ -n "$c" ] || return 0
    out="$(goh_unbind_git
           git -C "$1" rev-parse "$c:crates" "$c:Cargo.toml" "$c:Cargo.lock" "$c:rust-toolchain.toml" \
               2>/dev/null)" || return 0
    printf '%s' "$out" | tr '\n' ' ' | sed 's/ $//'
}

# goh_stamp_of <binary> -- what it says it was built from; empty for a pre-stamp build.
goh_stamp_of() {
    [ -x "$1" ] || return 0
    "$1" source-tree 2>/dev/null | head -n1 || true
}
