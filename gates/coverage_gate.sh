#!/usr/bin/env bash
# coverage_gate.sh — ONE parameterized coverage gate replacing six drifting
# per-repo copies.
#
#   coverage_gate.sh --lang rust|swift|cpp|py [--floor N] [--ignore RE]
#                    [--engine spm|xcodebuild] [path]
#
# Swift mode (implemented in gates/coverage_swift.py):
#   --engine spm          swift test + llvm-cov (default; GOH_COV_SWIFT_ENGINE)
#   --engine xcodebuild   xcodebuild test -enableCodeCoverage YES -scheme S
#                         (GOH_COV_SCHEME), llvm-cov on the profdata xcodebuild
#                         emits under GOH_COV_DD (default .build/xcode-dd).
#                         Missing xcodebuild is exit 2 naming it.
#   GOH_COV_XCRESULT      skip the run: totals from an existing xcresult via
#                         xccov. xccov has NO line-level data, so cov:ignore
#                         markers cannot be honored there and their presence
#                         is a named exit 2 — never silent forgiveness.
#
# Region forgiveness (swift mode): source lines marked
#   // cov:ignore: <reason>
#   // cov:ignore-start: <reason> ... // cov:ignore-end
# drop only UNCOVERED marked lines from the denominator. A marker without a
# reason, or a start without an end, is a gate error (exit 2).
#
# Floor resolution (first wins):
#   1. --floor N
#   2. GOH_COV_FLOOR_<LANG>   (GOH_COV_FLOOR_RUST / _SWIFT / _CPP / _PY)
#   3. none → exit 2 naming both seams. A gate without a stated floor is a
#      gate that silently passes anything; we refuse to be that.
#
# --ignore is an exclusion REGEX passed through to the underlying tool
# VERBATIM. The gate never invents exclusions: a repo's ignore list belongs in
# the repo's own invocation (.gatesrc / gate.sh), each entry with its reason,
# per the house rule that untested-by-construction files are named, not swept.
#
# Lineage (ported, then unified):
#   rust  ← ~/Projects/app_updates/tools/coverage_check.sh (the best copy):
#           cargo llvm-cov with ONE lcov export PER TEST TARGET, then a merge
#           pass that strips CGU hashes so duplicate template/function
#           instantiations group together (llvm-cov emits one record per
#           instantiation; naive aggregation reports phantom misses from
#           zero-count clones of functions other clones demonstrably ran).
#           Reports EXACT uncovered lines when below floor. Its ancestor
#           ~/Projects/monitor/tools/coverage_check.sh was a single aggregated
#           export + --fail-under-lines — kept here only as contrast; its
#           phantom-miss failure mode is why the per-target exports exist.
#   swift ← necrohand tools/check_coverage.sh + ZeroThunder tools/run_coverage.sh:
#           swift test --enable-code-coverage → xcrun llvm-cov export
#           -summary-only over the .xctest binary's merged profdata, JSON
#           totals parse (positional text columns shift across Xcode releases;
#           JSON does not). The two ancestors disagreed (summary JSON vs
#           per-file gated report); ONE coherent approach chosen: summary-only
#           totals vs the floor. Per-file gating can layer on later.
#   cpp   ← CadGoose2 scripts/check_coverage.sh: CMake CODE_COVERAGE=ON build,
#           ctest run under LLVM_PROFILE_FILE, llvm-profdata merge, llvm-cov
#           report parse of the TOTAL line percent. The ancestor's per-tier
#           thresholds (P0/P1/total) collapse here to ONE floor — tiers are a
#           repo policy, not gate plumbing; a repo needing tiers keeps its own
#           eligible-list wrapper around this script.
#   py    ← sys_updater ci.sh pytest-cov pattern, invoked through the coverage
#           CLI instead of the pytest plugin so --ignore maps verbatim onto
#           coverage's --omit (pytest-cov has no omit flag; the alternative
#           would be dropping the regex on the floor, i.e. inventing policy).
#
# Exit codes: 0 pass · 1 coverage below floor · 2 usage/config error.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOH_ROOT="$(cd "$HERE/.." && pwd)"

# Kare styling: vendored tui/lib.sh in the target repo if present, else ours.
for _tui in "${GOH_GIT_ROOT:-}/tui/lib.sh" tui/lib.sh "$GOH_ROOT/tui/lib.sh"; do
    # shellcheck disable=SC1090  # vendored lib resolved at runtime, as _common.sh
    [ -n "$_tui" ] && [ -f "$_tui" ] && . "$_tui" && break
done
unset _tui
info() { printf '→ %s\n' "$*"; }
ok() { printf '✓ %s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*" >&2; }
err() { printf '✗ %s\n' "$*" >&2; }
command -v _lib_info >/dev/null 2>&1 && {
    info() { _lib_info "$*"; }; ok() { _lib_ok "$*"; }
    warn() { _lib_warn "$*"; }; err() { _lib_err "$*"; };
}
die() { err "coverage_gate: $*"; exit 2; }

usage() {
    cat <<'EOF'
usage: coverage_gate.sh --lang rust|swift|cpp|py [--floor N] [--ignore RE]
                        [--engine spm|xcodebuild] [path]

  --lang L     one of: rust swift cpp py (required)
  --floor N    minimum LINE coverage percent (else $GOH_COV_FLOOR_<LANG>)
  --ignore RE  exclusion regex, passed verbatim to the toolchain
  --engine E   swift only: spm (default) | xcodebuild
               ($GOH_COV_SWIFT_ENGINE; scheme via GOH_COV_SCHEME,
                existing xcresult via GOH_COV_XCRESULT)
  path         project root (default: $PWD)

exit: 0 pass | 1 below floor | 2 usage/config error
EOF
}

LANG_=""
FLOOR=""
IGNORE=""
ENGINE=""
ENGINE_EXPLICIT=""
PROJ=""

while [ $# -gt 0 ]; do
    case "$1" in
        --lang)   [ $# -ge 2 ] || die "--lang needs a value"; LANG_="$2"; shift 2 ;;
        --floor)  [ $# -ge 2 ] || die "--floor needs a value"; FLOOR="$2"; shift 2 ;;
        --ignore) [ $# -ge 2 ] || die "--ignore needs a value"; IGNORE="$2"; shift 2 ;;
        --engine) [ $# -ge 2 ] || die "--engine needs a value"; ENGINE="$2"; ENGINE_EXPLICIT=1; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --*)      die "unknown option: $1 (see --help)" ;;
        *)        PROJ="$1"; shift ;;
    esac
done

[ -n "$LANG_" ] || { usage >&2; die "--lang is required (rust|swift|cpp|py)"; }
case "$LANG_" in
    rust|swift|cpp|py) ;;
    *) die "unknown --lang '$LANG_' (valid: rust swift cpp py)" ;;
esac

# Swift engine seam: flag beats GOH_COV_SWIFT_ENGINE, default spm. An engine
# is meaningless outside the swift mode — reject it there too rather than
# silently ignoring what a user explicitly asked for.
if [ -z "$ENGINE" ]; then
    ENGINE="${GOH_COV_SWIFT_ENGINE:-spm}"
fi
case "$ENGINE" in
    spm|xcodebuild) ;;
    *) die "unknown engine '$ENGINE' (valid: spm xcodebuild; flag or GOH_COV_SWIFT_ENGINE)" ;;
esac
if [ -n "$ENGINE_EXPLICIT" ] && [ "$LANG_" != "swift" ]; then
    die "--engine applies to --lang swift only (got $LANG_)"
fi

ENV_VAR=""
if [ -z "$FLOOR" ]; then
    ENV_VAR="GOH_COV_FLOOR_$(printf '%s' "$LANG_" | tr '[:lower:]' '[:upper:]')"
    FLOOR="${!ENV_VAR:-}"
fi
if [ -z "$FLOOR" ]; then
    die "no coverage floor for '$LANG_': pass --floor N or set ${ENV_VAR:-GOH_COV_FLOOR_<LANG>}"
fi
case "$FLOOR" in
    ''|*[!0-9.]*) die "floor must be numeric, got '$FLOOR'${ENV_VAR:+ (from $ENV_VAR)}" ;;
esac

[ -n "$PROJ" ] || PROJ="$PWD"
[ -d "$PROJ" ] || die "project path is not a directory: $PROJ"
PROJ="$(cd "$PROJ" && pwd)"

# floor_cmp <pct> — exit 1 (gate failure) if pct < FLOOR, else say ok.
floor_cmp() {
    _pct="$1"
    _below=$(awk -v p="$_pct" -v f="$FLOOR" 'BEGIN { print (p+0 < f+0) ? 1 : 0 }')
    if [ "$_below" = "1" ]; then
        err "line coverage $_pct% is below the $FLOOR% floor"
        return 1
    fi
    ok "line coverage $_pct% (floor $FLOOR%)"
}

need() { # need <cmd> <what-it-is-for>
    command -v "$1" >/dev/null 2>&1 || {
        err "coverage_gate: '$1' not found on PATH — required for the $LANG_ mode ($2)"
        exit 2
    }
}

# ── rust ────────────────────────────────────────────────────────────────────
# Ported from app_updates/tools/coverage_check.sh; see lineage above. One lcov
# export PER TEST TARGET, unioned by the CGU-normalizing merger below.
run_rust() {
    cd "$PROJ"
    need cargo "cargo llvm-cov drives the instrumented build"
    need python3 "the lcov merge post-processor"

    # Coverage builds must NOT share the global cargo target dir: stale
    # non-instrumented artifacts silently zero out whole modules. sccache /
    # incremental caches can serve objects with mismatched instrumentation,
    # which manifests as duplicate zero-count function records.
    export CARGO_TARGET_DIR="$PROJ/target/llvm-cov"
    export RUSTC_WRAPPER=""
    export CARGO_INCREMENTAL=0
    cargo llvm-cov clean --workspace >/dev/null 2>&1 || rm -rf "$CARGO_TARGET_DIR"

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
for p in meta["packages"]:
    for t in p["targets"]:
        if "lib" in t["kind"]:
            print(f"{p['name']}\tlib\t")
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
            if cargo llvm-cov -p "$PKG" --lib --all-features \
                    ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                    --lcov --output-path "$part" \
                    >/dev/null 2>&1; then
                : >"$part.ok"
            else
                warn "$label export failed"
            fi
        else
            # Part name carries the package: two crates with same-named test
            # targets must not overwrite each other's lcov part.
            info "exporting $PKG (test $TNAME)"
            part="$PARTS/part-$PKG-$TNAME.info"
            label="$PKG (test $TNAME)"
            if cargo llvm-cov -p "$PKG" --test "$TNAME" --all-features \
                    ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                    --lcov --output-path "$part" \
                    >/dev/null 2>&1; then
                : >"$part.ok"
            else
                warn "$label export failed"
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
            part="$PARTS/part-$PKG-$TNAME.info"
            label="$PKG (test $TNAME)"
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

    # The merge + floor decision lives in gates/lcov_merge.py: it strips CGU
    # hashes, groups instantiations, counts a line covered iff ANY
    # instantiation ran, and prints exact uncovered lines on failure.
    python3 "$GOH_ROOT/gates/lcov_merge.py" --floor "$FLOOR" "$PARTS"
}

# ── swift ───────────────────────────────────────────────────────────────────
# Implementation lives in gates/coverage_swift.py (both engines, one report
# processor); this wrapper keeps the shared arg/floor handling in bash.
run_swift() {
    cd "$PROJ"
    python3 "$GOH_ROOT/gates/coverage_swift.py" \
        --floor "$FLOOR" --proj "$PROJ" --engine "$ENGINE" \
        ${IGNORE:+--ignore "$IGNORE"} \
        || exit $?
}

# ── cpp ─────────────────────────────────────────────────────────────────────
run_cpp() {
    cd "$PROJ"
    need cmake "configure/build the CODE_COVERAGE=ON tree"
    need ctest "run the suite under the profiler"
    need xcrun "llvm-profdata merge + llvm-cov report"
    local build_dir="${GOH_CPP_BUILD_DIR:-build-cov}"

    info "cmake configure + build (CODE_COVERAGE=ON, $build_dir)"
    cmake -S "$PROJ" -B "$PROJ/$build_dir" -DCMAKE_BUILD_TYPE=Release \
        -DCODE_COVERAGE=ON >/dev/null 2>&1 \
        || { err "cmake configure failed"; exit 1; }
    cmake --build "$PROJ/$build_dir" >/dev/null 2>&1 \
        || { err "coverage build failed"; exit 1; }

    info "ctest (instrumented)"
    # GOH_CTEST_ARGS: extra ctest selection, verbatim. Repos label their
    # display-taking tests (house convention: LABELS "requires_display") and
    # their own automation runs `ctest -LE requires_display` — a coverage
    # measurement must drive the SAME suite, or it seizes the desktop and
    # measures flaky partial data instead. Empty by default: plain ctest.
    # Hard fail, not warn: coverage measured over a failing suite is partial
    # data wearing a green number. A release gate must not launder test
    # failures into a percentage.
    # shellcheck disable=SC2086
    (cd "$PROJ/$build_dir" && \
        LLVM_PROFILE_FILE="$PROJ/$build_dir/default-%p.profraw" \
        ctest --output-on-failure ${GOH_CTEST_ARGS:-} >/dev/null 2>&1) \
        || { err "ctest reported failures — refusing to measure partial coverage"; exit 1; }

    local raws profdata
    raws=$(find "$PROJ/$build_dir" -maxdepth 1 -name 'default-*.profraw')
    [ -n "$raws" ] || { err "no .profraw written — instrumentation emitted no profiles"; exit 1; }
    profdata="$PROJ/$build_dir/default.profdata"
    # shellcheck disable=SC2086
    xcrun llvm-profdata merge -sparse $raws -o "$profdata" \
        || { err "profdata merge failed"; exit 1; }

    # Test binary: explicit seam first, else the unique *test* executable at
    # the top of the build dir. Ambiguity is an error, never a guess.
    local bin
    bin="${GOH_CPP_TEST_BIN:-}"
    if [ -z "$bin" ]; then
        local found
        found=$(find "$PROJ/$build_dir" -maxdepth 1 -type f -perm +111 -iname '*test*' ! -name '*.sh' || true)
        case "$(printf '%s\n' "$found" | grep -c . )" in
            0) die "no test executable in $build_dir — set GOH_CPP_TEST_BIN" ;;
            1) bin="$found" ;;
            *) die "multiple test executables in $build_dir — set GOH_CPP_TEST_BIN to pick one" ;;
        esac
    fi
    [ -x "$bin" ] || die "test binary not executable: $bin"
    need python3 "parsing llvm-cov's JSON summary"
    # LINE coverage, BY NAME from llvm-cov's JSON summary. Never positional:
    # this took the TOTAL row's LAST column, which is BRANCH coverage on a
    # branch-instrumented build (60.80% where lines were 71.21%), and the
    # column count is not fixed. See CHANGELOG.
    local pct
    pct=$(xcrun llvm-cov export "$bin" -instr-profile="$profdata" \
        ${IGNORE:+-ignore-filename-regex "$IGNORE"} -summary-only 2>/dev/null \
        | python3 -c 'import json,sys
try: print("%.2f" % json.load(sys.stdin)["data"][0]["totals"]["lines"]["percent"])
except Exception: pass' ) || true
    case "$pct" in
        ''|*[!0-9.]*) err "could not parse total line % from llvm-cov export"; exit 1 ;;
    esac

    floor_cmp "$pct"
}

# ── py ──────────────────────────────────────────────────────────────────────
run_py() {
    cd "$PROJ"
    need python3 "coverage + pytest"
    python3 -m coverage --version >/dev/null 2>&1 \
        || { err "'coverage' not installed (python3 -m pip install coverage pytest)"; exit 2; }
    python3 -m pytest --version >/dev/null 2>&1 \
        || { err "'pytest' not installed (python3 -m pip install coverage pytest)"; exit 2; }

    info "coverage run -m pytest"
    # shellcheck disable=SC2086
    python3 -m coverage run --source="$PROJ" \
        ${IGNORE:+--omit "$IGNORE"} \
        -m pytest -q >/dev/null 2>&1 \
        || { err "tests failed while collecting coverage"; exit 1; }

    # The floor is enforced BY coverage itself (--fail-under), and echoed here
    # in house voice so every mode reports the same shape.
    if python3 -m coverage report --fail-under="$FLOOR" >/dev/null 2>&1; then
        pct=$(python3 -m coverage report | tail -1 | awk '{print $NF}' | tr -d '%')
        floor_cmp "$pct"
    else
        pct=$(python3 -m coverage report | tail -1 | awk '{print $NF}' | tr -d '%')
        err "line coverage ${pct:-unknown}% is below the $FLOOR% floor"
        python3 -m coverage report --show-missing >&2 || true
        exit 1
    fi
}

case "$LANG_" in
    rust)  run_rust ;;
    swift) run_swift ;;
    cpp)   run_cpp ;;
    py)    run_py ;;
esac
