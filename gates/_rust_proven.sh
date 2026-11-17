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
# has narrower inputs (WHAT THE MEASURED NUMBER DEPENDS ON, below; `gate_identity.py rust`, the one
# formula). A green measurement is recorded under those; a run on
# the same Rust inputs reuses it and runs the tests PLAIN instead -- cargo's own targets, and the
# external suite against the plain build of these same inputs -- so a red test is still red. A
# Rust-input change, a binary that is not this tree's build, a dirty tree or GOH_PROVEN=0 measures.

# THE DECISION IS PRINTED, EVERY RUN, as a `↔` line (tui `measure`), the one kind a passing step's log
# still shows (gates/_common.sh _goh_surface_warns): reused and from when, or measured and why. The
# first land after GOH.RUSTCOVSKIP measured for 535.36 s of rust_gate's 564.40 s with not one line
# saying the reuse had been looked for (GOH.RUSTCOVREUSE): its step named the push worktree's path.
#
# WHAT THE MEASURED NUMBER DEPENDS ON, so what the key covers (GOH.RUSTCOVKEYSCOPE; the owner's
# rule, GOH.RUSTCOVSKIP: the suite is not an input). The number is the share of the binary's Rust
# lines the suite reaches, as the coverage scripts build, run, merge and judge it. Its inputs:
#   * the BINARY: the repo's Rust build inputs, embedded files included (`inputs`: the stamp's
#     formula, lib/goh_version.sh);
#   * the INSTRUMENT: the coverage scripts under the gates root and GOH_DIR both (`scripts`:
#     coverage_gate.sh's closure, rust_gate.sh, this file), the tools, the GOH_* configuration in
#     the environment (the step) and every GOH_COV_* .gatesrc sets, exported or not (`cov`: a floor
#     raised there must be judged, and the plain run judges no floor; the external command too);
#   * the RUNNER: the repo's files the external command runs the binary THROUGH (`runner`: goh's
#     ./tools/pytest.sh and what it sources, imports or runs -- which tests run, and how);
#   * the environment the build and the runner read (`env`, _proven.sh's list), with PYTHONPATH
#     read as the measurement reads it: a gates root on it by its coverage scripts' CONTENT, never
#     its path. goh's land puts the tip's head export, ~/.cache/goh/head/<sha>, first on it, and
#     keyed by path no land ever found the last one's measurement: three in a row measured,
#     468-476 s each, for lib/ and tests/ changes (GOH.RUSTCOVKEYSCOPE).
# NOT inputs, by the owner's rule: the suite (tests/, and the files Rust test modules embed) and
# whatever the binary or the suite runs while it is exercised (lib/, checks/, hooks/ beyond the
# files above). A change there moves what is exercised, not what is measured: the plain run still
# runs every test, so a red test is red, and the number is measured again at the next change to an
# input above. Anything that can move the number and is not keyed here is a defect of THIS list:
# never reuse a measurement whose real inputs changed -- the floor outranks the 470 s it costs.

# _rust_cov_inputs <step> -- the measurement's inputs on this tree, one line each (the key is their
# hash; a record's copy names what moved on the next miss); or why there are none, and 1.
_rust_cov_inputs() {
    local top ident extra="" v d args
    [ "$PROVEN_ON" = 1 ] || { echo "the proven cache is off (GOH_PROVEN=0)"; return 1; }
    [ -n "${GOH_COV_RUST_EXTERNAL:-}" ] || { echo "no external suite (GOH_COV_RUST_EXTERNAL)"; return 1; }
    # shellcheck disable=SC2154  # cargo_dir is the sourcing gate's (rust_gate.sh)
    top="$(cd "$cargo_dir" && git rev-parse --show-toplevel 2>/dev/null)" || { echo "not in a git checkout"; return 1; }
    [ -n "$(cd "$top" && proven_tree </dev/null)" ] || { echo "the tree is not clean"; return 1; }   # as every record
    # PYTHONPATH as an argument: gate_identity.py re-runs HEAD's copy, which rewrites the variable.
    args=(--external "$GOH_COV_RUST_EXTERNAL")
    [ -z "${PYTHONPATH+set}" ] || args+=(--pythonpath "$PYTHONPATH")
    ident="$(cd "$top" && "$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/gate_identity.py" rust --repo "$top" \
        "${args[@]}" </dev/null 2>&1)" || { echo "no identity: $(printf '%s\n' "$ident" | tail -n 1)"; return 1; }
    # GOH_DIR's path is not in the step (_rust_step), so its scripts are keyed by content when it is
    # another checkout than the one running this.
    d="$(cd "${GOH_DIR:-$PROVEN_GOH_ROOT}" 2>/dev/null && /bin/pwd -P)" || d=""
    if [ -n "$d" ] && [ "$d" != "$PROVEN_GOH_ROOT" ] && [ -f "$d/lib/gate_identity.py" ]; then
        extra="$(cd "$top" && "$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/gate_identity.py" rust --repo "$top" \
            --root "$d" </dev/null 2>&1 | grep '^scripts ')" || { echo "no identity for GOH_DIR's scripts"; return 1; }
    fi
    printf 'rust-coverage v3\nstep %s\n%s\n' "$1" "$ident"
    if [ -n "$extra" ]; then printf 'GOH_DIR %s\n' "$extra"; fi
    for v in $(compgen -v GOH_COV_); do
        case "$v" in GOH_COV_CACHE|GOH_COV_CACHE_*) continue ;; esac   # where, and how big: no verdict
        printf 'cov %s=%s\n' "$v" "${!v}"
    done
    # PYTHONPATH is the identity's line above, read by content (the live half names it nowhere).
    _proven_identity_live </dev/null
}

# rust_cov_key <step> -- prints the measurement's key on this tree's inputs (_rust_cov_inputs); or
# prints why no key can be made, and returns 1 (no reuse possible: it measures).
rust_cov_key() {
    local text
    text="$(_rust_cov_inputs "$1")" || { printf '%s\n' "$text"; return 1; }
    printf '%s\n' "$text" | hash_hex /dev/stdin
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

# Where each measurement's inputs are kept, beside the records, by key (rust_coverage_reuse).
_rust_cov_inputs_dir() {
    local dir
    dir="$(proven_dir)" || return 1
    printf '%s/cov-inputs\n' "$dir"
}

# _rust_cov_moved <kept inputs> <inputs now> <tree then> <tree now> -- the inputs that differ, as
# one phrase: each line by its label (`tool cargo`, `cov GOH_COV_FLOOR_RUST`), a line that keys
# files with the ones that moved between the two trees, as git names them.
_rust_cov_moved() {
    local label paths names out="" n top
    top="$(cd "$cargo_dir" && git rev-parse --show-toplevel 2>/dev/null)" || top=""
    # shellcheck disable=SC2016  # the awk program's own $0 and $1
    while IFS=$'\t' read -r label paths; do
        names=""
        if [ -n "$paths" ] && [ -n "$top" ] && [ -n "$3" ] && [ -n "$4" ]; then
            # shellcheck disable=SC2086  # one path per word: gate_identity.py lists them so
            names="$(git -C "$top" diff --name-only "$3" "$4" -- $paths 2>/dev/null | awk '
                NR <= 3 { printf "%s%s", (NR > 1 ? ", " : ""), $0 } END { if (NR > 3) printf ", %d more", NR - 3 }')" || names=""
        fi
        case "$label" in
            inputs) label="the Rust build inputs" ;;
            scripts) label="the coverage scripts" ;;
            runner) label="the suite's runner" ;;
            GOH_DIR) label="GOH_DIR's coverage scripts" ;;
            pythonpath) label="PYTHONPATH (its gates roots by content)" ;;
            xcode) label="the selected Xcode" ;;
            rust-coverage|rust-identity) label="the key's formula" ;;
            "tool "*) label="the tool ${label#tool }" ;;
            "cov "*|"env "*) label="${label#* }" ;;
            "keep "*) label="the kept file ${label#keep }" ;;
        esac
        out="${out:+$out; }$label${names:+ ($names)}"
    done < <(env LC_ALL=C awk '
        function label(line,   w, n) {
            n = split(line, w, " ")
            if (w[1] == "tool" || w[1] == "keep") return w[1] " " w[2]
            if (w[1] == "cov" || w[1] == "env") { sub(/=.*/, "", w[2]); return w[1] " " w[2] }
            return w[1]
        }
        function files(line,   w, n, i, s) {   # a line that keys files: <kind> <hash> <path>...
            n = split(line, w, " ")
            if (w[1] != "inputs" && w[1] != "scripts" && w[1] != "runner") return ""
            for (i = 3; i <= n; i++) s = s (s == "" ? "" : " ") w[i]
            return s
        }
        FNR == NR { k = label($0); then[k] = $0; order[++n] = k; next }
        { k = label($0); now[k] = $0; if (!(k in then)) order[++n] = k }
        END {
            for (i = 1; i <= n; i++) {
                k = order[i]
                if (k in seen) continue
                seen[k] = 1
                if (then[k] != now[k]) printf "%s\t%s\n", k, files(then[k]) " " files(now[k])
            }
        }' "$1" <(printf '%s\n' "$2"))
    printf '%s\n' "${out:-nothing it keys}"
}

# _rust_cov_miss <step> <inputs now> -- why a keyed lookup missed, from this workspace's newest
# measurement record: one under this very step (so the inputs differ: which ones, from the inputs
# kept with it), else one under another configuration, else none.
_rust_cov_miss() {
    local dir f line epoch step best="" best_epoch=-1 near="" near_epoch=-1 now tree kept moved
    dir="$(proven_dir)" || { echo "no record store"; return 0; }
    # Every record, read here rather than by a `grep "$dir"/* | xargs ls -t` pipeline: the store
    # holds directories too (made/, scope-extra/, cov-inputs/), grep exits 2 on them, and under
    # rust_gate.sh's pipefail the pipeline found NO record while three were there -- every goh land
    # said "no measurement of this workspace is recorded yet" (GOH.RUSTCOVKEYSCOPE).
    for f in "$dir"/*; do
        [ -f "$f" ] || continue
        epoch=""
        step=""
        while IFS= read -r line; do
            case "$line" in
                epoch=*) epoch="${line#epoch=}" ;;
                step=*) step="${line#step=}" ;;
            esac
        done <"$f"
        case "$epoch" in ""|*[!0-9]*) continue ;; esac
        if [ "$step" = "$1" ]; then
            if [ "$epoch" -gt "$best_epoch" ]; then best="$f"; best_epoch="$epoch"; fi
        elif [ "${step%% env=*}" = "${1%% env=*}" ]; then
            if [ "$epoch" -gt "$near_epoch" ]; then near="$f"; near_epoch="$epoch"; fi
        fi
    done
    now="$(proven_now)"
    if [ -n "$best" ]; then
        kept="$(_rust_cov_inputs_dir)/${best##*/}"
        if [ ! -f "$kept" ]; then
            echo "the last measurement ($(proven_age $((now - best_epoch))) ago) was on other inputs; which moved is not known: no inputs were kept with it"
            return 0
        fi
        tree="$(sed -n 's/^tree=//p' "$best")"
        moved="$(_rust_cov_moved "$kept" "$2" "$tree" "$(proven_tree </dev/null)")"
        if [ "$moved" = "nothing it keys" ]; then
            echo "the last measurement ($(proven_age $((now - best_epoch))) ago) on these inputs is past its age"
        else
            echo "the last measurement ($(proven_age $((now - best_epoch))) ago) was on other inputs: $moved moved"
        fi
        return 0
    fi
    if [ -n "$near" ]; then
        echo "the last measurement ($(proven_age $((now - near_epoch))) ago) was under another GOH_* configuration"
        return 0
    fi
    echo "no measurement of this workspace is recorded yet"
}

# rust_coverage_reuse <measure-fn> <plain-fn> -- the plain run on a recorded measurement's inputs
# (RUST_COV_PLAIN_BINS naming the plain build), else the measurement, recorded when green.
rust_coverage_reuse() {
    local measure="$1" plain="$2" step cov_in="" key="" why="" hit age by bins kept
    step="$(_rust_step coverage-measure)"
    if ! cov_in="$(_rust_cov_inputs "$step")"; then
        why="$cov_in"
        cov_in=""
    else
        key="$(printf '%s\n' "$cov_in" | hash_hex /dev/stdin)"
        if hit="$(proven_lookup "$key" "$step" </dev/null)"; then
            if bins="$(rust_cov_plain_bins)"; then
                read -r age by <<<"$hit"
                measure "[rust] coverage: reused -- measured on these Rust inputs $age ago by $by; the tests run plain"
                RUST_COV_PLAIN_BINS="$bins" "$plain"
                return
            fi
            why="a measurement is recorded, but no plain build of these inputs to run the tests against: $bins"
        else
            why="$(_rust_cov_miss "$step" "$cov_in")"
        fi
    fi
    measure "[rust] coverage: measuring -- $why"
    "$measure" || return
    [ -n "$key" ] || return 0
    if [ "$(rust_cov_key "$step")" != "$key" ]; then
        info "[rust] coverage: the Rust inputs moved while they were measured -- not recorded"
        return 0
    fi
    if ! proven_record "$key" "$(proven_tree </dev/null)" "$step" "rust_gate coverage" </dev/null; then
        warn "[rust] could not write the proven record for the coverage measurement"
        return 0
    fi
    # The inputs, kept by key so the next miss names what moved; one per live record.
    if ! { kept="$(_rust_cov_inputs_dir)" && mkdir -p "$kept" && printf '%s\n' "$cov_in" >"$kept/$key"; }; then
        warn "[rust] could not keep the coverage measurement's inputs beside its record"
        return 0
    fi
    local f
    for f in "$kept"/*; do
        if [ -f "$f" ] && [ ! -f "${kept%/*}/${f##*/}" ]; then rm -f "$f"; fi
    done
    return 0
}
