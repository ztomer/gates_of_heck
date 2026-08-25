#!/usr/bin/env bash
# coverage_gate.sh — ONE parameterized coverage gate replacing six drifting
# per-repo copies.
#
#   coverage_gate.sh --lang rust|swift|cpp|py [--floor N] [--ignore RE] [path]
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
usage: coverage_gate.sh --lang rust|swift|cpp|py [--floor N] [--ignore RE] [path]

  --lang L     one of: rust swift cpp py (required)
  --floor N    minimum LINE coverage percent (else $GOH_COV_FLOOR_<LANG>)
  --ignore RE  exclusion regex, passed verbatim to the toolchain
  path         project root (default: $PWD)

exit: 0 pass | 1 below floor | 2 usage/config error
EOF
}

LANG_=""
FLOOR=""
IGNORE=""
PROJ=""

while [ $# -gt 0 ]; do
    case "$1" in
        --lang)   [ $# -ge 2 ] || die "--lang needs a value"; LANG_="$2"; shift 2 ;;
        --floor)  [ $# -ge 2 ] || die "--floor needs a value"; FLOOR="$2"; shift 2 ;;
        --ignore) [ $# -ge 2 ] || die "--ignore needs a value"; IGNORE="$2"; shift 2 ;;
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

    # Package discovery: a crates/ workspace enumerates members; a single-root
    # package is itself. Packages without src/lib.rs skip the lib export
    # (pure-bin crates have no lib target).
    if [ -d crates ]; then
        PKG_DIRS=$(find crates -maxdepth 1 -mindepth 1 -type d | sort)
    else
        PKG_DIRS="."
    fi

    for PKG_DIR in $PKG_DIRS; do
        PKG=$(sed -n 's/^name = "\(.*\)"$/\1/p' "$PKG_DIR/Cargo.toml" 2>/dev/null | head -1)
        [ -z "$PKG" ] && continue
        if [ -f "$PKG_DIR/src/lib.rs" ]; then
            info "exporting $PKG (lib unittests)"
            cargo llvm-cov -p "$PKG" --lib --all-features \
                ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                --lcov --output-path "$PARTS/part-$PKG-lib.info" \
                >/dev/null 2>&1 || warn "$PKG lib export failed"
        fi
        while IFS= read -r t; do
            TBASE=$(basename "$t" .rs)
            info "exporting $PKG (test $TBASE)"
            cargo llvm-cov -p "$PKG" --test "$TBASE" --all-features \
                ${IGNORE:+--ignore-filename-regex "$IGNORE"} \
                --lcov --output-path "$PARTS/part-$TBASE.info" \
                >/dev/null 2>&1 || warn "$PKG/$TBASE export failed"
        done < <(find "$PKG_DIR/tests" -name '*.rs' 2>/dev/null)
    done

    n_parts=$(find "$PARTS" -name 'part-*.info' | wc -l | tr -d ' ')
    [ "$n_parts" -gt 0 ] || {
        err "no lcov parts were exported — nothing was measured (is there a lib.rs or tests/? )"
        exit 1
    }

    # The merge + floor decision lives in python: it strips CGU hashes,
    # groups instantiations, counts a line covered iff ANY instantiation ran,
    # and prints exact uncovered lines on failure.
    GOH_FLOOR="$FLOOR" python3 - <<'PYEOF'
import glob
import os
import re
import sys
from collections import defaultdict

parts = sorted(glob.glob(os.path.join(os.environ["CARGO_TARGET_DIR"], "lcov-parts", "part-*.info")))
floor = float(os.environ["GOH_FLOOR"])


def normalize(mangled: str) -> str:
    """Strip the CGU hash so duplicate instantiations group together."""
    return re.sub(r"Cs[0-9A-Za-z]+_", "Cs_", mangled)


line_best = defaultdict(int)   # (file, line) -> max count across exports
fn_best = defaultdict(int)     # (file, norm_name) -> max FNDA
fn_start = {}                  # (file, norm_name) -> first start line

for pf in parts:
    cur = None
    das = []
    for raw in open(pf):
        line = raw.strip()
        if line.startswith("SF:"):
            cur = line[3:]
        elif cur is None:
            continue
        elif line.startswith("FN:") and "," in line[3:]:
            head, name = line[3:].split(",", 1)
            start = int(head.split(",")[0])
            key = (cur, normalize(name))
            fn_start.setdefault(key, start)
        elif line.startswith("FNDA:"):
            cnt, name = line[5:].split(",", 1)
            key = (cur, normalize(name))
            fn_best[key] = max(fn_best[key], int(cnt))
        elif line.startswith("DA:") and cur:
            p = line[3:].split(",")
            das.append((cur, int(p[0]), int(p[1])))
    for f, ln, cnt in das:
        line_best[(f, ln)] = max(line_best[(f, ln)], cnt)

# Executed spans per file: a function spans from its start line to the line
# before the next function's start; a line inside a span whose FNDA>0 anywhere
# is covered even if its own DA row shows 0 (duplicate zero-count clone).
spans = defaultdict(list)      # file -> [(start, end, executed)]
by_file_fn = defaultdict(dict)
for (f, name), st in fn_start.items():
    by_file_fn[f][name] = st
for f, names in by_file_fn.items():
    ordered = sorted(set(names.values()))
    for name, st in names.items():
        nxt = min([s for s in ordered if s > st], default=None)
        end = (nxt - 1) if nxt is not None else 10**9
        spans[f].append((st, end, fn_best.get((f, name), 0) > 0))

missed = defaultdict(list)
total = len(line_best)
covered = 0
for (f, ln), cnt in line_best.items():
    if cnt > 0:
        covered += 1
        continue
    for st, end, executed in spans.get(f, []):
        if st <= ln <= end and executed:
            covered += 1
            break
    else:
        missed[f].append(ln)

if total == 0:
    print("✗ [coverage] no coverable lines found in any lcov part")
    sys.exit(1)

pct = round(100.0 * covered / total, 2)
below = pct < floor
print(("✗" if below else "✓") + f" [coverage] {pct}% of coverable lines (floor {floor:g}%, {covered}/{total} lines)")
if below:
    print("✗ [coverage] uncovered lines:")
    for f, lines in sorted(missed.items()):
        print(f"  {f}: {', '.join(map(str, lines))}")
sys.exit(1 if below else 0)
PYEOF
}

# ── swift ───────────────────────────────────────────────────────────────────
run_swift() {
    cd "$PROJ"
    need swift "swift test --enable-code-coverage"
    need xcrun "xcrun llvm-cov reads the profdata"
    need python3 "JSON totals parse"

    info "swift test --enable-code-coverage"
    swift test --enable-code-coverage >/dev/null 2>&1 || {
        err "tests failed while collecting coverage"
        exit 1
    }
    local bin_path xctest profdata
    bin_path=$(swift build --show-bin-path)
    xctest=$(find "$bin_path" -maxdepth 4 -path '*.xctest/Contents/MacOS/*' -type f | head -1)
    [ -n "$xctest" ] || die "no .xctest binary under $bin_path — coverage build produced none"
    profdata="$bin_path/codecov/default.profdata"
    [ -f "$profdata" ] || die "no profdata at $profdata — swift test did not emit coverage"

    local pct
    pct=$(xcrun llvm-cov export -summary-only "$xctest" \
        -instr-profile="$profdata" \
        ${IGNORE:+-ignore-filename-regex="$IGNORE"} \
        | python3 -c 'import json,sys; print(round(json.load(sys.stdin)["data"][0]["totals"]["lines"]["percent"], 2))') \
        || { err "could not parse llvm-cov JSON summary"; exit 1; }

    floor_cmp "$pct"
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
    (cd "$PROJ/$build_dir" && \
        LLVM_PROFILE_FILE="$PROJ/$build_dir/default-%p.profraw" \
        ctest --output-on-failure >/dev/null 2>&1) \
        || warn "ctest reported failures — coverage data may be partial"

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

    local report pct
    report=$(xcrun llvm-cov report "$bin" -instr-profile="$profdata" \
        ${IGNORE:+-ignore-filename-regex "$IGNORE"} 2>/dev/null) \
        || { err "llvm-cov report failed"; exit 1; }
    pct=$(printf '%s\n' "$report" | tail -1 | awk '{print $NF}' | tr -d '%')
    case "$pct" in
        ''|*[!0-9.]*) err "could not parse total line % from llvm-cov report"; exit 1 ;;
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
