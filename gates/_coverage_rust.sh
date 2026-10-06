# shellcheck shell=bash
# _coverage_rust.sh -- coverage_gate.sh's rust mode (run_rust), sourced, never run. Split out for
# the 500-line cap; it uses the gate's own need/err/info/export_failed and its PROJ/IGNORE/... vars.

# ── rust ────────────────────────────────────────────────────────────────────
# Ported from app_updates/tools/coverage_check.sh; see lineage above. ONE instrumented run of
# every declared lib/bin/test target, its lcov fed to the CGU-normalizing merger below.
run_rust() {
    cd "$PROJ" || exit 2
    need cargo "cargo llvm-cov drives the instrumented build"
    need cargo-llvm-cov "the instrumented build and the lcov export -- cargo install cargo-llvm-cov"
    need python3 "the lcov merge post-processor"

    # Coverage builds must NOT share the global cargo target dir: stale
    # non-instrumented artifacts silently zero out whole modules. sccache /
    # incremental caches can serve objects with mismatched instrumentation,
    # which manifests as duplicate zero-count function records.
    #
    # The BUILD dir too: since cargo's `build.build-dir`, the target dir holds
    # only final artifacts while every intermediate -- including the test
    # binaries cargo-llvm-cov exports from -- lives in the build dir, which a
    # machine-wide setting (`~/.cargo/config.toml`) shares across all builds
    # of this crate. Pinning only CARGO_TARGET_DIR left the instrumented
    # binaries of the PREVIOUS source in that shared dir, and the export
    # merged them in: lines past the end of the current file, all uncovered,
    # a 96% tree reading 93.5% (routines, 2026-09-21).
    # The caller's values of what this mode overrides: --external's CMD runs in THEM (see below).
    CALLER_CARGO_ENV="$(for v in CARGO_TARGET_DIR CARGO_BUILD_BUILD_DIR RUSTC_WRAPPER CARGO_INCREMENTAL; do
        if [ -n "${!v+x}" ]; then printf 'export %s=%q\n' "$v" "${!v}"; else printf 'unset %s\n' "$v"; fi
    done)"
    export CARGO_TARGET_DIR="$PROJ/target/llvm-cov"
    export CARGO_BUILD_BUILD_DIR="$CARGO_TARGET_DIR"
    export RUSTC_WRAPPER=""
    export CARGO_INCREMENTAL=0
    # The PROFILES are reset, the BUILD is not. `clean --workspace` rebuilt every member
    # instrumented on every run (mediaops-rs: 20 s cleaned, 12 s not, identical report). With the
    # target AND build dir pinned per project (above), cargo-llvm-cov reports only the current
    # build's objects: a deleted test target, another metadata hash (a version bump) and removed
    # code each read exactly as after a clean build, and a previous run's profile -- which WOULD
    # carry a dropped call forward -- is removed here (tests/test_coverage_incremental.py).
    cargo llvm-cov clean --profraw-only >/dev/null 2>&1 || {
        err "could not clear the previous run's profiles -- its counts would be this run's"
        exit 1
    }

    # ONE RUN, ONE EXPORT. The rust mode exported one lcov part PER TEST TARGET (`--no-clean`, the
    # profile reset between targets) and unioned them. The phantom misses that design was credited
    # with come from aggregating duplicate instantiations, and the CGU-normalizing merger fixes
    # those whatever the part count: A/B on all 29 media_server crates (2026-10-06), one combined
    # run of the same targets gave the IDENTICAL merged report -- same percentage, same covered and
    # coverable line counts, every crate -- for 173 s against 226 s, one cargo invocation and one
    # export per crate instead of one per target.

    # THE EXTERNAL PART (--external / GOH_COV_RUST_EXTERNAL): a crate whose behaviour suite drives
    # its binary from outside `cargo test` (gates_of_heck: the pytest suite runs `goh` thousands of
    # times) was measured by its Rust tests alone -- 62.5% for code the suite covers. The binaries
    # are built instrumented in a target dir of their own (`cargo llvm-cov show-env`), CMD runs with
    # $GOH_COVERAGE_BIN_DIR naming them and LLVM_PROFILE_FILE set, and the profiles it leaves are
    # one more part. A failing CMD is a failed part, never a silently smaller report.
    # CMD runs in the CALLER's environment: it is a suite that builds crates of its own, and the
    # build's (RUSTC_WRAPPER, CARGO_LLVM_COV*, this target dir) made each of them an instrumented
    # build in the wrong place and a nested coverage gate fail. It gains only the profile
    # destination -- a pool of files, not one per process (`%p`: 326 files, 474 MB per suite run).
    external_part() {
        local label="external: $EXTERNAL" part="$PARTS/part-external.info"
        info "exporting $label"
        if (
            export CARGO_TARGET_DIR="$PROJ/target/llvm-cov-external"
            export CARGO_BUILD_BUILD_DIR="$CARGO_TARGET_DIR"
            showenv="$(cargo llvm-cov show-env --sh 2>/dev/null)" || exit 1
            eval "$showenv"
            cargo llvm-cov clean --profraw-only >/dev/null 2>&1 || true
            cargo build --locked --bins >"$part.log" 2>&1 || exit 1
            bins="$CARGO_TARGET_DIR/debug" profile="${LLVM_PROFILE_FILE/-%p/}"
            (
                for v in $(printf '%s\n' "$showenv" | sed -n 's/^export \([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); do
                    unset "$v"
                done
                eval "$CALLER_CARGO_ENV"
                LLVM_PROFILE_FILE="$profile" GOH_COVERAGE_BIN_DIR="$bins" bash -c "$EXTERNAL"
            ) >>"$part.log" 2>&1 || exit 1
            cargo llvm-cov report ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                --lcov --output-path "$part" >>"$part.log" 2>&1
        ); then
            : >"$part.ok"
        else
            export_failed "$label" "$part.log"
        fi
    }
    PARTS="$CARGO_TARGET_DIR/lcov-parts"
    rm -rf "$PARTS"; mkdir -p "$PARTS"

    # Package + target discovery: ASK CARGO, never the filesystem. A
    # `find tests -name '*.rs'` walk reports FILE names; cargo metadata
    # reports TARGETS — and they diverge exactly when a suite uses
    # directory-style test targets (`tests/foo/main.rs`, target `foo`) or a
    # non-autodiscovered file. Every divergence is an export naming a target
    # that does not exist, i.e. silently lost coverage. (Found 2026-08-25:
    # app_updates had split golden suites into directory targets; the walk
    # produced `--test main` for every one of them.)
    info "enumerating workspace packages/targets via cargo metadata"
    METAF="$CARGO_TARGET_DIR/targets.tsv"
    python3 - >"$METAF" <<'PYEOF'
import json
import subprocess

meta = json.loads(subprocess.run(
    ["cargo", "metadata", "--no-deps", "--format-version", "1"],
    check=True, capture_output=True, text=True).stdout)
# A bin target's unit tests are a coverage source too: a bin-only crate
# (no lib) has ALL its unit tests there, and until 2026-09-14 this loop
# never exported them, so such a crate read as "no coverable lines".
for p in meta["packages"]:
    for t in p["targets"]:
        if "lib" in t["kind"]:
            print(f"{p['name']}\tlib\t")
        elif "bin" in t["kind"]:
            print(f"{p['name']}\tbin\t{t['name']}")
        elif "test" in t["kind"]:
            print(f"{p['name']}\ttest\t{t['name']}")
PYEOF

    # Which target KINDS exist, from the enumeration above: the run selects exactly those (no
    # examples, no benches, no doctests), as the per-target exports did.
    KINDS=""
    grep -q '	lib	' "$METAF" && KINDS="$KINDS --lib"
    grep -q '	bin	' "$METAF" && KINDS="$KINDS --bins"
    grep -q '	test	' "$METAF" && KINDS="$KINDS --tests"
    WORKSPACE_PART="$PARTS/part-workspace.info"
    if [ -n "$KINDS" ]; then
        info "exporting every target in one instrumented run: $(cut -f1,3 "$METAF" | tr '\t\n' ': ' | sed 's/: / /g')"
        # Completeness marker: written ONLY on cargo-llvm-cov exit 0. A failing run that leaves a
        # stale or partial file behind must not pass as measured data -- the marker is the proof.
        # shellcheck disable=SC2086  # KINDS is a list of flags
        if cargo llvm-cov --locked --no-clean --workspace $KINDS --all-features \
                ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                --lcov --output-path "$WORKSPACE_PART" \
                >"$WORKSPACE_PART.log" 2>&1; then
            : >"$WORKSPACE_PART.ok"
        else
            export_failed "the workspace's targets" "$WORKSPACE_PART.log"
        fi
    fi
    if [ -n "${EXTERNAL:-}" ]; then
        external_part
    fi

    # The run must have EXITED 0, proven by its .ok marker. Keying completeness on part-file
    # existence or size was a LAUNDERING HOLE: cargo-llvm-cov can fail AFTER creating the output
    # file, leaving garbage or a partial export that then counted as coverage ("100%" over
    # nothing). The ONLY warn-only case is a marker'd valid-but-empty part (targets with nothing
    # coverable in them): the run succeeded, it simply measures zero lines.
    MISSING=""
    if [ -n "$KINDS" ]; then
        if [ ! -f "$WORKSPACE_PART.ok" ]; then
            MISSING="$(cut -f1,2,3 "$METAF" | awk -F'\t' '{printf "%s%s (%s%s)", (NR>1?" ":""), $1, $2, ($3==""?"":" " $3)}')"
        elif [ ! -s "$WORKSPACE_PART" ]; then
            warn "the workspace's targets exported a valid-but-EMPTY lcov part — nothing coverable was measured"
        fi
    fi
    if [ -n "${EXTERNAL:-}" ] && [ ! -f "$PARTS/part-external.info.ok" ]; then
        MISSING="${MISSING}${MISSING:+ }external: $EXTERNAL"
    fi
    if [ -n "$MISSING" ]; then
        err "coverage exports incomplete — expected but missing (failed or never written):${MISSING}"
        err "  each failed export above is lost coverage — fix it, do not trust this run"
        exit 1
    fi

    _MERGE_ARGS=()
    if [ -n "$FLOOR" ]; then _MERGE_ARGS+=(--floor "$FLOOR"); fi
    if [ -n "$FLOORS_JSON" ]; then _MERGE_ARGS+=(--floors-json "$FLOORS_JSON"); fi
    if [ -n "$INCLUDE" ]; then _MERGE_ARGS+=(--include "$INCLUDE"); fi
    if [ -n "$MARKER_CEILING" ]; then _MERGE_ARGS+=(--marker-ceiling "$MARKER_CEILING"); fi
    python3 "$GOH_ROOT/gates/lcov_merge.py" "${_MERGE_ARGS[@]}" "$PARTS"
}
