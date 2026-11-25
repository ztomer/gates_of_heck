# shellcheck shell=bash disable=SC2154  # cargo_dir, PROVEN_ON, PROVEN_GOH_ROOT, GOH_PY: the sourcing gate's
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
#
# GOH.GATEFLOOR: the keys of `rust_gate.sh`'s groups keyed on THEIR OWN inputs, sourced by
# `_rust_proven.sh`. `compile` (the crate group: fmt, clippy, the manifest lints) keys `goh
# rust-scope --compile` (what the compiler reads: packages, config, `include!`, build scripts;
# dep-info checked and learned after a green run); `tests` (a land's `cargo test (plain)`) keys the
# tree minus `GOH_RUST_TEST_UNREAD`. Both key the gates by `rust_gate.sh`'s closure
# (`lib/rust_runner_id.py`), not the whole goh content, plus the resolved goh's stamp and the
# environment. Pinned by `test_rust_gate_scoped_cache.py`.
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_rust_inputs.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# gates/_rust_inputs.sh -- the keys of rust_gate.sh's groups that are keyed on THEIR OWN inputs, not on
# the gates that run them (GOH.GATEFLOOR). Sourced by gates/_rust_proven.sh; pinned by
# tests/test_rust_gate_floor.py.
#
# THE COST. Every proven key holds the whole goh content (gates/_proven_identity.sh). A goh land runs
# under the head export of main, so a land changing any lib/ or gates/ file -- Python only, Rust
# untouched -- changed the key of every Rust group of the land after it; and goh's crate opens
# CARGO_MANIFEST_DIR/../.. in its tests, so `goh rust-scope` keys its crate group on the whole tree.
# Both clippy runs (10-18 s each) ran in GOH.DURATIONSPRUNE's land, which changed tests/ alone, and
# `cargo test (plain)` (41-69 s) sat in the tree-keyed coverage group, run by every land.
#
# TWO MODES, each keyed on exactly what decides it:
#   compile  a group that compiles and runs no test (the `crate` group: fmt, clippy, the manifest
#            lints): `goh rust-scope --compile` -- the packages, their config, what an include! or a
#            build script reads -- checked after a green run against the compiler's own dep-info
#            (a read outside it is learned and keys the next run, as the crate mode's);
#   tests    `cargo test`: a Rust test reads the tree at run time, and goh's run goh's own scripts
#            (GOH_DIR is the checkout), so the WHOLE tree, minus the paths no Rust test reads
#            (GOH_RUST_TEST_UNREAD in .gatesrc: git pathspecs; tests/test_rust_gate_floor.py holds
#            goh's to it -- no Rust source names one).
# Beside them, both: the gates that run the group (lib/rust_runner_id.py: rust_gate.sh's
# closure by content, PYTHONPATH as it reads it, the tools), the goh binary the steps run (its stamp),
# the group's step and the goh configuration (_rust_step), and the environment (_proven_identity_live).
# A dirty tree, no closure, no scope: no key, and the group simply runs.

RUST_COMPILE_SCOPE_FILE=""
_rust_compile_scope_tried=""

_rust_compile_scope_init() {
    [ -z "$_rust_compile_scope_tried" ] || return 0
    _rust_compile_scope_tried=1
    [ "$PROVEN_ON" = 1 ] || return 0
    goh_resolve_native
    [ -n "$goh_native" ] || { info "[rust] compile scope off: ${goh_native_why:-no native goh}"; return 0; }
    local f learned
    GOH_PROVEN_KEYED_ROOTS="$(proven_keyed_roots)"
    export GOH_PROVEN_KEYED_ROOTS
    f="$(mktemp "$(goh_tmp)/goh-rust-compile-scope.XXXXXX")"
    goh_cleanup_add "$f" "$f.err"
    if "$goh_native" rust-scope --compile "$cargo_dir" >"$f" 2>"$f.err" && [ -s "$f" ]; then
        RUST_COMPILE_SCOPE_FILE="$f"
        learned="$(_rust_learned_file)" && [ -s "$learned" ] && cat "$learned" >>"$f"
    else
        info "[rust] compile scope off: $(head -n 1 "$f.err" 2>/dev/null)"
        rm -f "$f"
    fi
    rm -f "$f.err"
}

# _rust_inputs_lines <compile|tests> <step> -- the key's text on this tree; 1 when there is none.
_rust_inputs_lines() {
    local mode="$1" step="$2" tree top e roots=() d runner unread=() listed
    [ "${PROVEN_ON:-1}" = 1 ] || return 1
    tree="$(proven_tree </dev/null)" && [ -n "$tree" ] || return 1
    top="$(git rev-parse --show-toplevel 2>/dev/null)" || return 1
    printf 'rust_gate inputs v1 %s\nstep %s\n' "$mode" "$step"
    case "$mode" in
        compile)
            [ -n "$RUST_COMPILE_SCOPE_FILE" ] || return 1
            # One cat-file for every entry; a missing one is keyed `missing` (as proven_scoped_key).
            listed="$(set -o pipefail; while IFS= read -r e; do
                [ -n "$e" ] || continue
                if [ "$e" = . ]; then printf '%s\n' "$tree"; else printf '%s:%s\n' "$tree" "$e"; fi
            done <"$RUST_COMPILE_SCOPE_FILE" | git cat-file --batch-check='%(objectname) %(objecttype)' 2>/dev/null \
                | sed 's/^.* missing$/missing/')" && [ -n "$listed" ] || return 1
            printf '%s\n' "$listed"
            sed 's/^/scope /' "$RUST_COMPILE_SCOPE_FILE" ;;
        tests)
            # shellcheck disable=SC2086  # pathspecs, one per word: .gatesrc names them so
            for e in ${GOH_RUST_TEST_UNREAD:-}; do unread+=(":(exclude,glob)$e"); done
            # The index, which proven_tree has just found to be the working tree and $tree:
            # ls-tree takes no pathspec magic. A listing that fails is no key, never an empty one.
            listed="$(goh_bind_hook_index; git ls-files --stage --full-name -- \
                ":(top)" ${unread[@]+"${unread[@]}"} 2>&1)" && [ -n "$listed" ] || return 1
            printf 'tree %s\n' "$(printf '%s\n' "$listed" | hash_hex /dev/stdin)" ;;
        *) return 1 ;;
    esac
    while IFS= read -r d; do [ -n "$d" ] && roots+=(--root "$d"); done < <(_proven_goh_roots)
    [ "${#roots[@]}" -gt 0 ] || return 1
    local args=("${roots[@]}" --repo "$top")
    [ -z "${PYTHONPATH+set}" ] || args+=(--pythonpath "$PYTHONPATH")
    runner="$("$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/rust_runner_id.py" "${args[@]}" </dev/null 2>/dev/null)" || return 1
    printf '%s\n' "$runner"
    if [ -x "${GOH_RESOLVED_BIN:-}" ]; then
        printf 'goh-bin %s\n' "$(goh_stamp_of "$GOH_RESOLVED_BIN")"
    else
        printf 'goh-bin none\n'
    fi
    _proven_identity_live </dev/null
}

# _rust_inputs_key <compile|tests> <step> -- prints "<key> <tree>" (as _rust_key's other modes).
_rust_inputs_key() {
    local text tree
    text="$(_rust_inputs_lines "$1" "$2")" || return 1
    tree="$(proven_tree </dev/null)" || return 1
    printf '%s %s\n' "$(printf '%s\n' "$text" | hash_hex /dev/stdin)" "$tree"
}
