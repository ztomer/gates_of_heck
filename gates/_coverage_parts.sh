# shellcheck shell=bash
# _coverage_parts.sh -- the rust coverage mode's PARTS and their MERGE (GOH.CIWALL), sourced by
# _coverage_rust.sh, never run.
#
# WHY. Hosted CI ran the whole instrumented suite in one job: 2080-2526 s of a 49.6 min median wall
# on a 3-core runner. A public repo's parallel jobs are free, so .github/workflows/gate.yml splits the
# measurement the way it splits the work, and joins it where the verdict is made:
#   GOH_COV_PART=workspace  the workspace's targets only (cargo's own tests), no floor;
#   GOH_COV_PART=K/N        the external suite's K-th of N shards only (tools/pytest.sh runs
#                           `--goh-shard K/N`, tests/_shard.py), as part-external-K-of-N.info;
#   GOH_COV_PARTS_IN=DIR    no build, no tests: the parts in DIR merged and judged -- the floor, the
#                           per-crate summary and the change's lines, exactly as one run judges them.
# A part is never a verdict: it says the floor is the merge's. The merge is red unless the parts are
# COMPLETE -- the workspace's and every external shard 1..N of one N, each with its .ok marker -- so a
# shard whose artifact never arrived is "coverage exports incomplete", never a smaller report. A
# line's verdict is its best count over the parts (gates/lcov_merge.py), so N shards of the suite and
# the suite in one run read the same covered lines (tests/test_coverage_parts.py).
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# cov_part_mode -- read the two variables into COV_PART_ONLY (workspace|external|""), COV_SHARD
# (K/N|""), COV_EXT_PART (the external part's file stem) and COV_MERGE_IN (an absolute dir|"").
cov_part_mode() {
    COV_PART_ONLY="" COV_SHARD="" COV_EXT_PART="part-external" COV_MERGE_IN=""
    local part="${GOH_COV_PART:-}" k n
    if [ -n "$part" ] && [ -n "${GOH_COV_PARTS_IN:-}" ]; then
        err "GOH_COV_PART and GOH_COV_PARTS_IN are one run each: a part is made, or parts are merged"
        return 2
    fi
    case "$part" in
        "") ;;
        workspace) COV_PART_ONLY=workspace ;;
        *)
            k="${part%%/*}" n="${part#*/}"
            case "$k/$n" in
                [1-9]*/[1-9]*) ;;
                *) k="" ;;
            esac
            if [ -z "$k" ] || [ "$k/$n" != "$part" ] || [ "${k//[0-9]/}${n//[0-9]/}" != "" ] \
                || [ "$k" -gt "$n" ]; then
                err "GOH_COV_PART='$part': give 'workspace' or K/N with 1 <= K <= N"
                return 2
            fi
            [ -n "${EXTERNAL:-}" ] || { err "GOH_COV_PART=$part shards the external suite, and there is none (GOH_COV_RUST_EXTERNAL)"; return 2; }
            # shellcheck disable=SC2034  # read by _coverage_rust.sh, which sources this file
            COV_PART_ONLY=external COV_SHARD="$part" COV_EXT_PART="part-external-$k-of-$n"
            ;;
    esac
    if [ -n "${GOH_COV_PARTS_IN:-}" ]; then
        case "$GOH_COV_PARTS_IN" in
            /*) COV_MERGE_IN="$GOH_COV_PARTS_IN" ;;
            *) COV_MERGE_IN="$PROJ/$GOH_COV_PARTS_IN" ;;   # relative: to the project, as gate.yml names it
        esac
        [ -d "$COV_MERGE_IN" ] || { err "GOH_COV_PARTS_IN=$GOH_COV_PARTS_IN is not a directory ($COV_MERGE_IN)"; return 2; }
    fi
    return 0
}

# cov_parts_complete <dir> -- the parts a merge needs, every one with its marker; prints what is
# missing and returns 1, else 0. The shard count is read from the parts' own names: one N for all.
cov_parts_complete() {
    local dir="$1" f name n="" k missing=""
    [ -f "$dir/part-workspace.info.ok" ] || missing="the workspace's targets (part-workspace.info)"
    for f in "$dir"/part-external-*-of-*.info; do
        [ -e "$f" ] || continue
        name="${f##*/part-external-}" name="${name%.info}"
        if [ -z "$n" ]; then n="${name#*-of-}"; elif [ "$n" != "${name#*-of-}" ]; then
            err "the external parts name two shard counts ($n and ${name#*-of-}): one run's parts only"
            return 1
        fi
    done
    if [ -z "$n" ]; then
        if [ -n "${EXTERNAL:-}" ]; then
            missing="${missing}${missing:+; }every external shard (part-external-K-of-N.info)"
        fi
    else
        for ((k = 1; k <= n; k++)); do
            [ -f "$dir/part-external-$k-of-$n.info.ok" ] \
                || missing="${missing}${missing:+; }external shard $k of $n (part-external-$k-of-$n.info)"
        done
    fi
    if [ -n "$missing" ]; then
        err "coverage exports incomplete — expected but missing (failed or never written): $missing"
        err "  each missing part is lost coverage — fix it, do not trust this run"
        return 1
    fi
    info "merging $(find "$dir" -maxdepth 1 -name 'part-*.info' | wc -l | tr -d ' ') coverage parts from $dir${n:+ (the external suite in $n shards)}"
}

# cov_judge_parts -- the floor, the audit claim and the change's lines over $PARTS: one run's tail,
# and the whole of a merge's.
cov_judge_parts() {
    # A SUBSET run (--diff-only, a land's selected tests: lib/land_rustcov.py, GOH.LANDRUSTCOV)
    # judges no floor and claims nothing: a part of the suite is no crate's percentage, and a
    # claim of it would be a carry the fresh run disagrees with. Only the change's lines.
    if [ -n "${DIFF_ONLY:-}" ]; then
        cov_phase "diff coverage" ""
        local only_rc=0
        "$GOH_PY" "$GOH_ROOT/gates/diff_cov.py" ${INCLUDE:+--include "$INCLUDE"} --repo "$PROJ" "$PARTS" \
            || only_rc=$?
        cov_phase ""
        return "$only_rc"
    fi
    _MERGE_ARGS=()
    if [ -n "$FLOOR" ]; then _MERGE_ARGS+=(--floor "$FLOOR"); fi
    if [ -n "$FLOORS_JSON" ]; then _MERGE_ARGS+=(--floors-json "$FLOORS_JSON"); fi
    if [ -n "$INCLUDE" ]; then _MERGE_ARGS+=(--include "$INCLUDE"); fi
    if [ -n "$MARKER_CEILING" ]; then _MERGE_ARGS+=(--marker-ceiling "$MARKER_CEILING"); fi
    cov_phase "merge + floor" ""
    local merge_rc=0 diff_rc=0
    "$GOH_PY" "$GOH_ROOT/gates/lcov_merge.py" "${_MERGE_ARGS[@]}" --summary "$PARTS/summary.json" \
        "$PARTS" || merge_rc=$?
    # THE CLAIM AND ITS AUDIT (GOH.COVCARRY2, the owner's term 3; lib/prof_carry.py). A suite run
    # outside cargo test stands on carried verdicts: a green merge claims its per-crate summary for
    # this tree, and the fresh full instrumented run (lib/full_run.py INSTRUMENTED, which says so in
    # GOH_PROFCARRY_AUDIT=fresh) compares its own with that claim instead -- a disagreement turns
    # the carry off. Neither ever changes this step's verdict.
    if [ "$merge_rc" = 0 ] && [ -n "${EXTERNAL:-}" ] && [ -s "$PARTS/summary.json" ]; then
        local verb=claim said
        [ "${GOH_PROFCARRY_AUDIT:-}" != fresh ] || verb=compare
        said="$("$GOH_PY" "$GOH_ROOT/lib/prof_carry.py" "$verb" --repo "$PROJ" \
            --summary "$PARTS/summary.json" --by "coverage step" </dev/null 2>&1)" || true
        info "${said:-coverage audit: $verb said nothing}"
    fi
    # THE CHANGE'S LINES (GOH.DIFFCOV): a second, separate reader of the same parts -- every
    # executable Rust line the change adds or edits is covered, named file:line when not
    # (gates/diff_cov.py; GOH_COV_DIFF on|warn|off). It reads the report, never makes it.
    cov_phase "diff coverage" ""
    "$GOH_PY" "$GOH_ROOT/gates/diff_cov.py" ${INCLUDE:+--include "$INCLUDE"} --repo "$PROJ" "$PARTS" \
        || diff_rc=$?
    cov_phase ""
    [ "$merge_rc" = 0 ] || return "$merge_rc"   # the EXIT trap (cov_reap) reaps the scratch
    return "$diff_rc"
}

# cov_merge_only -- GOH_COV_PARTS_IN: judge the parts a run's shards made, building nothing.
cov_merge_only() {
    PARTS="$COV_MERGE_IN"
    exec 3>&1
    COV_PHASE_FILE="$PARTS/.phase"   # as a run's: beside the parts it judges
    : >"$COV_PHASE_FILE" || return 2
    cov_parts_complete "$PARTS" || return 1
    cov_judge_parts
}
