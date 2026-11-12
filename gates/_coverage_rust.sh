# shellcheck shell=bash
# _coverage_rust.sh -- coverage_gate.sh's rust mode (run_rust), sourced, never run. Split out for
# the 500-line cap; it uses the gate's own need/err/info/export_failed and its PROJ/IGNORE/... vars.

# ── rust ────────────────────────────────────────────────────────────────────
# Ported from app_updates/tools/coverage_check.sh; see lineage above. ONE instrumented run of
# every declared lib/bin/test target, its lcov fed to the CGU-normalizing merger below.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
# ── THE PHASES, STREAMED (GOH.COVPHASES) ─────────────────────────────────────
# A step killed on its ceiling shows the tail of what it printed, and this mode printed three intro
# lines and then nothing for half an hour: each part's output goes to its own log, read only when
# the part FAILS. Hosted run 43320855869 was cut at 1800 s with no word of which of its builds,
# test runs and reports had the time. So every phase says when it starts and how long it took, on
# the gate's own stdout (fd 3: a part's stdout is its log) as it happens, and a stop signal names the
# phase it landed in, its elapsed, and the tail of that phase's log (tests/test_coverage_phases.py).
cov_phase() { # cov_phase <name> <log>: begin <name>, ending the phase in progress; "" ends only
    local now
    now="$(date +%s)"
    if [ -s "$COV_PHASE_FILE" ]; then
        local t0 name
        IFS=$'\t' read -r t0 name _ <"$COV_PHASE_FILE"
        printf '%s\n' "  ✓ phase '$name': $((now - t0)) s" >&3
    fi
    if [ -n "$1" ]; then
        printf '%s\t%s\t%s\n' "$now" "$1" "${2:-}" >"$COV_PHASE_FILE"
        printf '%s\n' "  → phase '$1' started $(date -u +%H:%M:%SZ)" >&3
    else
        : >"$COV_PHASE_FILE"
    fi
}
cov_phase_failed() { # a part that failed: its phase in progress is named as the one that failed
    local t0 name
    if [ -s "$COV_PHASE_FILE" ]; then
        IFS=$'\t' read -r t0 name _ <"$COV_PHASE_FILE"
        printf '%s\n' "  ✗ phase '$name' failed after $(($(date +%s) - t0)) s" >&3
        : >"$COV_PHASE_FILE"
    fi
}
cov_phase_stopped() { # the stop-signal trap: name the phase the signal landed in, then exit 128+N
    local t0 name log
    trap - TERM INT HUP
    if [ -s "$COV_PHASE_FILE" ]; then
        IFS=$'\t' read -r t0 name log <"$COV_PHASE_FILE"
        printf '%s\n' "✗ coverage stopped by SIG$1 in phase '$name' after $(($(date +%s) - t0)) s" >&3
        if [ -n "$log" ] && [ -s "$log" ]; then
            printf '%s\n' "    tail of $log:" >&3
            tail -n "${GOH_TAIL:-25}" "$log" | sed 's/^/    /' >&3
        fi
    else
        printf '%s\n' "✗ coverage stopped by SIG$1 between phases" >&3
    fi
    case "$1" in INT) exit 130 ;; HUP) exit 129 ;; *) exit 143 ;; esac
}
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
    # THE BUILD DIR OUTSIDE THE TREE, keyed on the tree's path (BACKLOG 5.1). Both dirs were
    # `$PROJ/target/llvm-cov`, and the push gate runs in a worktree it deletes after every push: every
    # crate's instrumented build began cold on every push, its registry dependencies included, while
    # clippy (cargo's own build dir, keyed on the same stable worktree path) stayed warm. mediaops-rs,
    # a push-shaped re-checkout: 40.7 s of CPU cold, 19.2 s with the build dir kept, the same report.
    # The TARGET dir stays in the tree: it holds the binaries a test runs, and media_server's find
    # their repo by walking up from their own path (`env_mini::exe_root`); out of the tree, two of
    # mediaops' CLI tests failed. Per-project isolation (above) holds -- the key is the project's
    # physical path. Pruned like the HEAD exports (gates/_head_cache.py): a week unused, past the
    # newest 200, or past GOH_COV_CACHE_MAX_GIB (default 8; 0 = no byte budget) -- each only once
    # idle for an hour.
    COV_CACHE="${GOH_COV_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/goh/llvm-cov}"
    COV_KEY="$(pwd -P | shasum -a 256 | cut -c1-16)"
    mkdir -p "$COV_CACHE/$COV_KEY"
    : >"$COV_CACHE/$COV_KEY/.goh-used"
    "$GOH_PY" -S -c 'import sys; sys.path.insert(0, sys.argv[1]); import _head_cache; gib = float(sys.argv[4]); _head_cache.prune(sys.argv[2], sys.argv[3], keep=200, max_bytes=int(gib * 2**30) if gib > 0 else None)' \
        "$(dirname "${BASH_SOURCE[0]}")" "$COV_CACHE" "$COV_KEY" "${GOH_COV_CACHE_MAX_GIB:-8}" 2>/dev/null || true
    export CARGO_TARGET_DIR="$PROJ/target/llvm-cov"
    export CARGO_BUILD_BUILD_DIR="$COV_CACHE/$COV_KEY/build"
    export RUSTC_WRAPPER=""
    export CARGO_INCREMENTAL=0
    # The PROFILES are reset, the BUILD is not. `clean --workspace` rebuilt every member
    # instrumented on every run (mediaops-rs: 20 s cleaned, 12 s not, identical report). With the
    # target AND build dir pinned per project (above), cargo-llvm-cov reports only the current
    # build's objects: a deleted test target, another metadata hash (a version bump) and removed
    # code each read exactly as after a clean build, and a previous run's profile -- which WOULD
    # carry a dropped call forward -- is removed here (tests/test_coverage_incremental.py).
    # (The clearing runs in the show-env environment below, where this run's profiles land.)

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
            goh_unbind_git   # the suite is a test run: it never sees git's repository variables (GOH.HOOKGITENV)
            export CARGO_TARGET_DIR="$PROJ/target/llvm-cov-external"
            export CARGO_BUILD_BUILD_DIR="$COV_CACHE/$COV_KEY/external"
            showenv="$(cargo llvm-cov show-env --sh 2>/dev/null)" || exit 1
            eval "$showenv"
            cargo llvm-cov clean --profraw-only >/dev/null 2>&1 || true
            cov_phase "external: instrumented build" "$part.log"
            # OPTIMIZED (GOH.COVPHASES): CMD drives the binary the way a user does -- the plain
            # suite drives a release build -- and an unoptimized one made every call 5-20x slower
            # (an `emoji` scan: 70 ms release, 1,439 ms debug-instrumented, 271 ms instrumented at
            # opt-level 2). Counters are placed before LLVM optimizes, so what they count is the
            # same; the debug profile, its assertions and its target dir are kept. `--config`, not
            # an exported variable: CMD's own builds never see it.
            cargo build --locked --bins --config profile.dev.opt-level=2 \
                --message-format=json-render-diagnostics \
                >"$part.json" 2>"$part.log" 3>&- || exit 1
            "$GOH_PY" "$GOH_ROOT/gates/_cov_prune.py" "$part.json" >>"$part.log" 2>&1 3>&- || exit 1
            cov_phase "$label" "$part.log"
            bins="$CARGO_TARGET_DIR/debug" profile="${LLVM_PROFILE_FILE/-%p/}"
            (
                for v in $(printf '%s\n' "$showenv" | sed -n 's/^export \([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); do
                    unset "$v"
                done
                eval "$CALLER_CARGO_ENV"
                LLVM_PROFILE_FILE="$profile" GOH_COVERAGE_BIN_DIR="$bins" bash -c "$EXTERNAL" 3>&-
            ) >>"$part.log" 2>&1 || exit 1
            cov_phase "external: report" "$part.log"
            cargo llvm-cov report ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                --lcov --output-path "$part" >>"$part.log" 2>&1 3>&- || exit 1
            cov_phase ""
        ); then
            : >"$part.ok"
        else
            cov_phase_failed
            export_failed "$label" "$part.log"
        fi
    }
    PARTS="$CARGO_TARGET_DIR/lcov-parts"
    rm -rf "$PARTS"; mkdir -p "$PARTS"
    exec 3>&1
    COV_PHASE_FILE="$PARTS/.phase"
    : >"$COV_PHASE_FILE"
    trap 'cov_phase_stopped TERM' TERM
    trap 'cov_phase_stopped INT' INT
    trap 'cov_phase_stopped HUP' HUP

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
    "$GOH_PY" - >"$METAF" <<'PYEOF'
import json
import subprocess

meta = json.loads(subprocess.run(
    ["cargo", "metadata", "--no-deps", "--format-version", "1"],
    check=True, capture_output=True, text=True).stdout)
# A bin target's unit tests are a coverage source too: a bin-only crate
# (no lib) has ALL its unit tests there, and until 2026-09-14 this loop
# never exported them, so such a crate read as "no coverable lines".
# A lib's kind is its crate-type list when the manifest declares one (["rlib", "cdylib"]),
# never "lib" then -- and an FFI crate whose tests are all unit tests read the same way.
LIB_KINDS = {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}
for p in meta["packages"]:
    for t in p["targets"]:
        if LIB_KINDS & set(t["kind"]):
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
        # BUILD, PRUNE, RUN, REPORT -- cargo-llvm-cov's show-env flow, the same instrumented
        # environment its one-shot mode sets (both are `set_env`), split so the build can be asked
        # what it produced. The build dir outlives a build (above), and the report reads EVERY
        # binary of a member's name it finds there, a previous build's included: _cov_prune.py
        # deletes the binaries cargo's own artifact list does not name
        # (tests/test_coverage_incremental.py::test_a_binary_of_another_build_never_counts).
        # shellcheck disable=SC2086  # KINDS is a list of flags
        if (
            goh_unbind_git   # the suite is a test run: it never sees git's repository variables (GOH.HOOKGITENV)
            eval "$(cargo llvm-cov show-env --sh 2>/dev/null)" && [ -n "${LLVM_PROFILE_FILE:-}" ] || exit 1
            cargo llvm-cov clean --profraw-only || {
                echo "could not clear the previous run's profiles -- its counts would be this run's"
                exit 1
            }
            cov_phase "workspace: instrumented build" "$WORKSPACE_PART.log"
            cargo test --no-run --locked --workspace $KINDS --all-features \
                --message-format=json-render-diagnostics >"$WORKSPACE_PART.json" 3>&- || exit 1
            "$GOH_PY" "$GOH_ROOT/gates/_cov_prune.py" "$WORKSPACE_PART.json" 3>&- || exit 1
            cov_phase "workspace: cargo test" "$WORKSPACE_PART.log"
            cargo test --locked --workspace $KINDS --all-features 3>&- || exit 1
            cov_phase "workspace: report" "$WORKSPACE_PART.log"
            cargo llvm-cov report ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                --lcov --output-path "$WORKSPACE_PART" 3>&- || exit 1
            cov_phase ""
        ) >"$WORKSPACE_PART.log" 2>&1; then
            : >"$WORKSPACE_PART.ok"
        else
            cov_phase_failed
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
    cov_phase "merge + floor" ""
    local merge_rc=0
    "$GOH_PY" "$GOH_ROOT/gates/lcov_merge.py" "${_MERGE_ARGS[@]}" "$PARTS" || merge_rc=$?
    cov_phase ""
    return "$merge_rc"
}
