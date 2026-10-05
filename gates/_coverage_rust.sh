# shellcheck shell=bash
# _coverage_rust.sh -- coverage_gate.sh's rust mode (run_rust), sourced, never run. Split out for
# the 500-line cap; it uses the gate's own need/err/info/export_failed and its PROJ/IGNORE/... vars.

# ── rust ────────────────────────────────────────────────────────────────────
# Ported from app_updates/tools/coverage_check.sh; see lineage above. One lcov
# export PER TEST TARGET, unioned by the CGU-normalizing merger below.
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
    export CARGO_TARGET_DIR="$PROJ/target/llvm-cov"
    export CARGO_BUILD_BUILD_DIR="$CARGO_TARGET_DIR"
    export RUSTC_WRAPPER=""
    export CARGO_INCREMENTAL=0
    cargo llvm-cov clean --workspace >/dev/null 2>&1 || rm -rf "$CARGO_TARGET_DIR"

    # ONE BUILD, N PROFILES: built once (cleaned above), then `--no-clean` per target -- each export
    # recompiled the members, 16 x ~2 s on media_server's mediaops-rs. Only the PROFILE is reset
    # per target (`--no-clean` alone leaked `cli` into the `version` part). A/B on mediaops-rs: merged report identical.
    fresh_profile() {
        cargo llvm-cov clean --profraw-only >/dev/null 2>&1 || {
            err "could not clear the previous target's profile before $1 -- its part would carry it"
            exit 1
        }
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

    while IFS="	" read -r PKG KIND TNAME; do
        [ -n "$PKG" ] || continue
        if [ "$KIND" = "lib" ]; then
            info "exporting $PKG (lib unittests)"
            part="$PARTS/part-$PKG-lib.info"
            label="$PKG (lib unittests)"
            # Completeness marker: written ONLY on cargo-llvm-cov exit 0. A
            # failing export that leaves a stale/partial file behind must not
            # pass as measured data — the marker is the proof of success.
            fresh_profile "$label"
            if cargo llvm-cov --locked --no-clean -p "$PKG" --lib --all-features \
                    ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                    --lcov --output-path "$part" \
                    >"$part.log" 2>&1; then
                : >"$part.ok"
            else
                export_failed "$label" "$part.log"
            fi
        else
            # Part name carries the package AND kind: two crates with
            # same-named targets, or a bin and a test target sharing a name,
            # must not overwrite each other's lcov part.
            info "exporting $PKG ($KIND $TNAME)"
            part="$PARTS/part-$PKG-$KIND-$TNAME.info"
            label="$PKG ($KIND $TNAME)"
            fresh_profile "$label"
            if cargo llvm-cov --locked --no-clean -p "$PKG" "--$KIND" "$TNAME" --all-features \
                    ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                    --lcov --output-path "$part" \
                    >"$part.log" 2>&1; then
                : >"$part.ok"
            else
                export_failed "$label" "$part.log"
            fi
        fi
    done <"$METAF"

    # Every DECLARED target must have produced an EXPORT THAT EXITED 0,
    # proven by its .ok marker. Keying completeness on part-file existence or
    # size was a LAUNDERING HOLE: cargo-llvm-cov can fail AFTER creating the
    # output file, leaving garbage or a partial export that then counted as
    # coverage ("100%" over nothing). The ONLY warn-only case is a marker'd
    # valid-but-empty part (a target with nothing coverable in it): the
    # export succeeded, it simply measures zero lines.
    MISSING=""
    while IFS="	" read -r PKG KIND TNAME; do
        [ -n "$PKG" ] || continue
        if [ "$KIND" = "lib" ]; then
            part="$PARTS/part-$PKG-lib.info"
            label="$PKG (lib unittests)"
        else
            part="$PARTS/part-$PKG-$KIND-$TNAME.info"
            label="$PKG ($KIND $TNAME)"
        fi
        if [ ! -f "$part.ok" ]; then
            MISSING="${MISSING}${MISSING:+ }$label"
        elif [ ! -s "$part" ]; then
            warn "$label exported a valid-but-EMPTY lcov part — nothing coverable was measured for it"
        fi
    done <"$METAF"
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
