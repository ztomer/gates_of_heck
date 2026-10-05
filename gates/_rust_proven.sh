# shellcheck shell=bash
# _rust_proven.sh -- rust_gate.sh's proven cache, per group and per crate (BACKLOG P3). Sourced.
#
# THE COST. The proven cache keyed every step on the WHOLE tree, so one README line re-gated every
# crate, and media_server's 29 per-crate gates found no record of each other's repo-wide scans.
# Now each group of rust_gate.sh is proven once on its own inputs:
#   crate     the workspace at $cargo_dir and every package it reaches by `path =`, plus the
#             config cargo/clippy/rustfmt/the gates read (`goh rust-scope` names them);
#   repo      the whole tree, for the scans that read the whole repo;
#   coverage  like crate.
# A group that passed on the same inputs, under the same identity (gates checkout, toolchains,
# environment -- gates/_proven.sh) and the same GOH_* configuration, prints who proved it and is
# skipped. CI never sees these records.
#
# WHY IT STAYS HONEST, beyond _proven.sh's clean-tree precondition:
#   * a source reaching outside its crate at run time (`"../`, CARGO_MANIFEST_DIR + `..`) widens
#     the scope to the whole tree -- `goh rust-scope` sees it;
#   * after a green crate or coverage group, the compiler's own dep-info is read, and a build that
#     read any file outside the scope records NOTHING, naming the file;
#   * the inputs are keyed again after the group ran; if they moved, nothing is recorded;
#   * no native goh, or a scope it cannot name: the group simply runs, uncached, and says so.
# GOH_PROVEN=0 turns all of it off.

# shellcheck source=gates/_proven.sh
. "$HERE/_proven.sh"
# shellcheck source=gates/_goh_bin.sh
. "$HERE/_goh_bin.sh"
proven_settings || exit 2

RUST_SCOPE_FILE=""
_rust_scope_tried=""

_rust_scope_init() {
    [ -z "$_rust_scope_tried" ] || return 0
    _rust_scope_tried=1
    [ "$PROVEN_ON" = 1 ] || return 0
    goh_resolve_native
    if [ -z "$goh_native" ]; then
        info "per-crate proven cache off: ${goh_native_why:-no native goh}"
        return 0
    fi
    local f
    f="$(mktemp "${TMPDIR:-/tmp}/goh-rust-scope.XXXXXX")"
    if "$goh_native" rust-scope "$cargo_dir" >"$f" 2>"$f.err" && [ -s "$f" ]; then
        RUST_SCOPE_FILE="$f"
    else
        warn "per-crate proven cache off: $(head -n 1 "$f.err" 2>/dev/null)"
        rm -f "$f"
    fi
    rm -f "$f.err"
}

# The configuration a group's verdict depends on, as one line: every GOH_* in the environment
# (the timing instrument's own keys aside), hashed.
_rust_step() {
    local env_hash
    env_hash="$(env | grep '^GOH_' | grep -v '^GOH_TIMINGS' | LC_ALL=C sort | hash_hex /dev/stdin)"
    printf 'rust_gate v1 group=%s cargo_dir=%s env=%s\n' "$1" "${cargo_dir#"$PWD"/}" "$env_hash"
}

_rust_key() { # <mode> <step>
    if [ "$1" = crate ]; then
        [ -n "$RUST_SCOPE_FILE" ] || return 1
        # shellcheck disable=SC2046  # one entry per line, no spaces: rust-scope prints paths
        proven_scoped_key "$2" $(cat "$RUST_SCOPE_FILE")
    else
        proven_key "$2"
    fi
}

# rust_proven_group <group> <crate|tree> <function> -- run the function's steps unless proven.
rust_proven_group() {
    local group="$1" mode="$2" fn="$3" step pair="" pkey="" ptree="" hit age by escaped
    [ "$mode" = crate ] && _rust_scope_init
    step="$(_rust_step "$group")"
    if pair="$(_rust_key "$mode" "$step" </dev/null)"; then
        read -r pkey ptree <<<"$pair"
        if hit="$(proven_lookup "$pkey" "$step")"; then
            read -r age by <<<"$hit"
            ok "[rust] $group checks: proven on these inputs $age ago by $by -- skipped"
            return 0
        fi
    fi
    "$fn"
    [ -n "$pkey" ] || return 0
    if [ "$mode" = crate ]; then
        if ! escaped="$("$goh_native" rust-scope "$cargo_dir" --check-depinfo "$RUST_SCOPE_FILE" 2>&1)"; then
            warn "[rust] $group checks not recorded as proven: the build read files outside the"
            warn "  crate's scope, so a change there could not invalidate the record: $(printf '%s' "$escaped" | tr '\n' ' ')"
            return 0
        fi
    fi
    if [ "$(_rust_key "$mode" "$step" </dev/null)" != "$pkey $ptree" ]; then
        info "[rust] $group checks: the inputs moved while they ran -- not recorded"
        return 0
    fi
    proven_record "$pkey" "$ptree" "$step" "rust_gate" </dev/null \
        || warn "[rust] could not write the proven record for the $group checks"
}
