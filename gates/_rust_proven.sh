# shellcheck shell=bash
# _rust_proven.sh -- rust_gate.sh's proven cache, per group and per crate (BACKLOG P3). Sourced.
#
# THE COST. The proven cache keyed every step on the WHOLE tree, so one README line re-gated every
# crate, and a private server workspace's 29 per-crate gates found no record of each other's
# repo-wide scans. Now each group of rust_gate.sh is proven once on its own inputs:
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
#     read any file outside the scope records NOTHING, naming the file. A file INSIDE the
#     repository is then LEARNED: kept beside the records (scope-extra/), and keyed with the scope
#     from the next run on, before AND after it runs -- so the run that learns records nothing (the
#     file was not keyed before it ran) and the next one can. Without this a crate whose build.rs
#     reads `compose/` ran on every push forever (a private server workspace's vpn-watchdog-rs, 2026-10-06). A
#     read outside the repository has no git object to key on: never learned, never recorded;
#   * the inputs are keyed again after the group ran; if they moved, nothing is recorded;
#   * no native goh, or a scope it cannot name: the group simply runs, uncached, and says so.
# GOH_PROVEN=0 turns all of it off.

_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=gates/_proven.sh
. "$_goh_d/_proven.sh"
# shellcheck source=gates/_goh_bin.sh
. "$_goh_d/_goh_bin.sh"
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
    # A path dependency inside a checkout the identity already keys (the gates checkout: antiknob's
    # goh-tui, R3.13) is left to the identity rather than refusing the whole scope.
    GOH_PROVEN_KEYED_ROOTS="$(proven_keyed_roots)"
    export GOH_PROVEN_KEYED_ROOTS
    f="$(mktemp "${TMPDIR:-/tmp}/goh-rust-scope.XXXXXX")"
    goh_cleanup_add "$f" "$f.err"  # it was never removed: ~4,700 leaked (a private Swift app, 2026-10-06)
    # shellcheck disable=SC2154  # cargo_dir is the sourcing gate's (rust_gate.sh), set before this runs
    if "$goh_native" rust-scope "$cargo_dir" >"$f" 2>"$f.err" && [ -s "$f" ]; then
        RUST_SCOPE_FILE="$f"
        # What earlier builds of this workspace were seen to read beyond it (see the header).
        local learned
        learned="$(_rust_learned_file)" && [ -s "$learned" ] && cat "$learned" >>"$f"
    else
        warn "per-crate proven cache off: $(head -n 1 "$f.err" 2>/dev/null)"
        rm -f "$f"
    fi
    rm -f "$f.err"
}

# _rust_where -- the cargo workspace as the step names it: relative to the repo the gate runs in, `.`
# at its root. It was `${cargo_dir#"$PWD"/}`, which leaves the root's ABSOLUTE path, and every land
# gates in a push worktree of its own (~/.cache/goh/push/wt-<hash>/wt): no record made in one was
# ever found from the next, so goh re-measured its Rust coverage on every land (GOH.RUSTCOVREUSE).
_rust_where() {
    # shellcheck disable=SC2154  # cargo_dir is the sourcing gate's (rust_gate.sh)
    case "$cargo_dir" in
        "$PWD") printf '.\n' ;;
        "$PWD"/*) printf '%s\n' "${cargo_dir#"$PWD"/}" ;;
        *) printf '%s\n' "$cargo_dir" ;;
    esac
}

# Where this workspace's learned extra inputs live: one file per cargo dir, beside the records.
_rust_learned_file() {
    local dir where
    where="$(_rust_where)"
    dir="$(proven_dir)" || return 1
    printf '%s/scope-extra/%s\n' "$dir" "$(printf 'rust_gate scope-extra v1 %s' "$where" | hash_hex /dev/stdin)"
}

# _rust_learn <escaped paths...> -- 0 when every one is repo-relative and is now learned.
_rust_learn() {
    local learned e
    for e in "$@"; do
        case "$e" in /*) return 1 ;; esac
    done
    learned="$(_rust_learned_file)" || return 1
    mkdir -p "$(dirname "$learned")" || return 1
    { [ -f "$learned" ] && cat "$learned"; printf '%s\n' "$@"; } | env LC_ALL=C sort -u >"$learned.tmp.$$" \
        && mv "$learned.tmp.$$" "$learned"
}

# The configuration a group's verdict depends on, as one line: every GOH_* in the environment
# (the timing instrument's own keys aside), hashed. GOH_DIR's PATH is left out: the gates checkout
# that judges is keyed by its CONTENT (every key's identity names each goh root, gates/_proven.sh
# _proven_goh_roots; the measurement's names its scripts under both, rust_cov_key), and goh's own land
# runs the tip's head export, ~/.cache/goh/head/<sha>, a new path for every commit (GOH.RUSTCOVREUSE).
_rust_step() {
    local env_hash
    env_hash="$(goh_config_hash GOH_DIR)"  # GOH_* minus gates/verdict_free_keys.txt (gates/_hash.sh)
    # The repo group's scans read the whole repo whichever crate called them, so its step names no
    # crate: in a 29-crate push the first records it and the other 28 find the record.
    local where
    where="$(_rust_where)"
    [ "$1" = repo ] && where="(repo)"
    printf 'rust_gate v1 group=%s cargo_dir=%s env=%s\n' "$1" "$where" "$env_hash"
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
    local group="$1" mode="$2" fn="$3" step pair="" pkey="" ptree="" hit age by escaped live="" moved=0
    [ "$mode" = crate ] && _rust_scope_init
    step="$(_rust_step "$group")"
    if pair="$(_rust_key "$mode" "$step" </dev/null)"; then
        read -r pkey ptree <<<"$pair"
        live="$(_proven_identity_live </dev/null)"
        if hit="$(proven_lookup "$pkey" "$step")"; then
            read -r age by <<<"$hit"
            # The coverage decision is shown on a pass (`↔`), as rust_coverage_reuse's is.
            if [ "$group" = coverage ]; then
                measure "[rust] $group checks: proven on these inputs $age ago by $by -- skipped"
            else
                ok "[rust] $group checks: proven on these inputs $age ago by $by -- skipped"
            fi
            return 0
        fi
    fi
    "$fn"
    [ -n "$pkey" ] || return 0
    # GOH.KEEP1: a kept path the group rewrote is NAMED, and red unless the repo's ratchet carries
    # it. The ratchet names the group by this label, not by the step string, which carries a hash.
    [ "$(_rust_key "$mode" "$step" </dev/null)" = "$pkey $ptree" ] || moved=1
    local where
    where="$(_rust_where)"
    [ "$group" = repo ] && where="(repo)"
    proven_kept_check "rust_gate $group $where" "$live" "$moved" || return 1
    if [ "$mode" = crate ]; then
        if ! escaped="$("$goh_native" rust-scope "$cargo_dir" --check-depinfo "$RUST_SCOPE_FILE" 2>&1)"; then
            # shellcheck disable=SC2086  # one path per line, no spaces: rust-scope prints paths
            if _rust_learn $escaped; then
                info "[rust] $group checks not recorded this run: the build read $(printf '%s' "$escaped" | tr '\n' ' ')"
                info "  outside the crate's scope -- keyed on it from now on, so the next run can record"
            else
                warn "[rust] $group checks not recorded as proven: the build read files outside the"
                warn "  crate's scope, so a change there could not invalidate the record: $(printf '%s' "$escaped" | tr '\n' ' ')"
            fi
            return 0
        fi
    fi
    if [ "$moved" = 1 ]; then
        info "[rust] $group checks: $(proven_what_moved "$ptree" "$live" </dev/null) moved while they ran -- not recorded"
        return 0
    fi
    proven_record "$pkey" "$ptree" "$step" "rust_gate" </dev/null \
        || warn "[rust] could not write the proven record for the $group checks"
}

# ── THE RUST COVERAGE VERDICT, REUSED (GOH.RUSTCOVSKIP) ─────────────────────────────────────────
# With an external suite (GOH_COV_RUST_EXTERNAL: goh's pytest drives the binary) the coverage group
# reads the whole tree, so its record keys on the whole tree, and every goh land re-measured: 896.62
# s of instrumented build, suite and report for GOH.XEXITDETAIL's Python and shell. The MEASUREMENT
# has narrower inputs: the repo's Rust build inputs and the Rust gate's coverage scripts
# (`gate_identity.py rust`, the one formula). A green measurement is recorded under those; a run on
# the same Rust inputs reuses it and runs the tests PLAIN instead -- cargo's own targets, and the
# external suite against the plain build of these same inputs -- so a red test is still red. A
# Rust-input change, a binary that is not this tree's build, a dirty tree or GOH_PROVEN=0 measures.

# THE DECISION IS PRINTED, EVERY RUN, as a `↔` line (tui `measure`), the one kind a passing step's log
# still shows (gates/_common.sh _goh_surface_warns): reused and from when, or measured and why. The
# first land after GOH.RUSTCOVSKIP measured for 535.36 s of rust_gate's 564.40 s with not one line
# saying the reuse had been looked for (GOH.RUSTCOVREUSE): its step named the push worktree's path.
#
# WHAT COUNTS AS AN INPUT (the owner's set, GOH.RUSTCOVSKIP): the Rust build inputs, the Rust gate's
# coverage scripts (under the gates root and GOH_DIR both), the tools, the GOH_* configuration in
# the environment and the coverage configuration .gatesrc sets (every GOH_COV_* variable, exported
# or not: a floor raised in .gatesrc must be judged, and the plain run judges no floor). NOT an
# input: the external suite (tests/) and the scripts it drives. A test change therefore does not
# re-measure the Rust coverage number until the next change to one of the inputs above.

# rust_cov_key <step> -- prints the measurement's key on this tree's Rust inputs; or prints why no
# key can be made, and returns 1 (no reuse possible: it measures).
rust_cov_key() {
    local top ident extra="" v d
    [ "$PROVEN_ON" = 1 ] || { echo "the proven cache is off (GOH_PROVEN=0)"; return 1; }
    [ -n "${GOH_COV_RUST_EXTERNAL:-}" ] || { echo "no external suite (GOH_COV_RUST_EXTERNAL)"; return 1; }
    # shellcheck disable=SC2154  # cargo_dir is the sourcing gate's (rust_gate.sh)
    top="$(cd "$cargo_dir" && git rev-parse --show-toplevel 2>/dev/null)" || { echo "not in a git checkout"; return 1; }
    [ -n "$(cd "$top" && proven_tree </dev/null)" ] || { echo "the tree is not clean"; return 1; }   # as every record
    ident="$(cd "$top" && "$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/gate_identity.py" rust --repo "$top" \
        </dev/null 2>&1)" || { echo "no identity: $(printf '%s\n' "$ident" | tail -n 1)"; return 1; }
    # GOH_DIR's path is not in the step (_rust_step), so its scripts are keyed by content when it is
    # another checkout than the one running this.
    d="$(cd "${GOH_DIR:-$PROVEN_GOH_ROOT}" 2>/dev/null && /bin/pwd -P)" || d=""
    if [ -n "$d" ] && [ "$d" != "$PROVEN_GOH_ROOT" ] && [ -f "$d/lib/gate_identity.py" ]; then
        extra="$(cd "$top" && "$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/gate_identity.py" rust --repo "$top" \
            --root "$d" </dev/null 2>&1 | grep '^scripts ')" || { echo "no identity for GOH_DIR's scripts"; return 1; }
    fi
    { printf 'rust-coverage v2\nstep %s\n%s\n%s\n' "$1" "$ident" "$extra"
      for v in $(compgen -v GOH_COV_); do
          case "$v" in GOH_COV_CACHE|GOH_COV_CACHE_*) continue ;; esac   # where, and how big: no verdict
          printf 'cov %s=%s\n' "$v" "${!v}"
      done
      _proven_identity_live </dev/null; } | hash_hex /dev/stdin
}

# rust_cov_plain_bins -- the directory of a plain build of THIS tree's Rust: the resolved goh, when
# what it says it was built from is this tree's stamp (goh gating itself); else why not, and 1.
rust_cov_plain_bins() {
    local top stamp
    [ -x "${GOH_RESOLVED_BIN:-}" ] || { echo "no resolved goh"; return 1; }
    top="$(cd "$cargo_dir" && git rev-parse --show-toplevel 2>/dev/null)" || { echo "not in a git checkout"; return 1; }
    stamp="$(goh_stamp_at "$top" HEAD 2>/dev/null)" && [ -n "$stamp" ] || { echo "no stamp for HEAD"; return 1; }
    [ "$(goh_stamp_of "$GOH_RESOLVED_BIN")" = "$stamp" ] || { echo "the resolved goh is not this tree's build"; return 1; }
    dirname "$GOH_RESOLVED_BIN"
}

# _rust_cov_miss <step> -- why a keyed lookup missed, from this workspace's newest measurement record:
# one under this very step (so the inputs differ), else one under another configuration, else none.
_rust_cov_miss() {
    local dir f epoch now what
    dir="$(proven_dir)" || { echo "no record store"; return 0; }
    what="on other Rust inputs, coverage configuration or tools, or past its age"
    # shellcheck disable=SC2012  # record names are hex: ls -t is the newest-first order wanted
    f="$(grep -lxF -- "step=$1" "$dir"/* 2>/dev/null | xargs ls -t 2>/dev/null | awk 'NR == 1')" || f=""
    if [ -z "$f" ]; then
        what="under another GOH_* configuration"
        # shellcheck disable=SC2012
        f="$(grep -lF -- "step=${1%% env=*} env=" "$dir"/* 2>/dev/null | xargs ls -t 2>/dev/null | awk 'NR == 1')" || f=""
    fi
    if [ -z "$f" ]; then
        echo "no measurement of this workspace is recorded yet"
        return 0
    fi
    epoch="$(sed -n 's/^epoch=//p' "$f")"
    now="$(proven_now)"
    case "$epoch" in ""|*[!0-9]*) epoch="$now" ;; esac
    echo "the last measurement ($(proven_age $((now - epoch))) ago) was $what"
}

# rust_coverage_reuse <measure-fn> <plain-fn> -- the plain run on a recorded measurement's inputs
# (RUST_COV_PLAIN_BINS naming the plain build), else the measurement, recorded when green.
rust_coverage_reuse() {
    local measure="$1" plain="$2" step key="" why="" hit age by bins
    step="$(_rust_step coverage-measure)"
    if ! key="$(rust_cov_key "$step")"; then
        why="$key"
        key=""
    elif hit="$(proven_lookup "$key" "$step" </dev/null)"; then
        if bins="$(rust_cov_plain_bins)"; then
            read -r age by <<<"$hit"
            measure "[rust] coverage: reused -- measured on these Rust inputs $age ago by $by; the tests run plain"
            RUST_COV_PLAIN_BINS="$bins" "$plain"
            return
        fi
        why="a measurement is recorded, but no plain build of these inputs to run the tests against: $bins"
    else
        why="$(_rust_cov_miss "$step")"
    fi
    measure "[rust] coverage: measuring -- $why"
    "$measure" || return
    [ -n "$key" ] || return 0
    if [ "$(rust_cov_key "$step")" != "$key" ]; then
        info "[rust] coverage: the Rust inputs moved while they were measured -- not recorded"
        return 0
    fi
    proven_record "$key" "$(proven_tree </dev/null)" "$step" "rust_gate coverage" </dev/null \
        || warn "[rust] could not write the proven record for the coverage measurement"
}
