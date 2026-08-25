#!/usr/bin/env bash
# swift_gate.sh — the Swift gates every house Swift project must pass.
#
#   1. swiftlint --strict      (warnings are errors)
#   2. build with warnings-as-errors
#   3. test, with a coverage floor
#
# WARNINGS ARE ERRORS, AND THE BUILD MUST BE COLD. An incremental build reports
# only what it recompiled: a ZoneTilerWM migration looked clean at 0 warnings
# incrementally and produced 413 on a clean rebuild, because the untouched
# targets were never re-examined.
#
# GOH_SWIFT_COLD defaults to 1 — cold ON PURPOSE. These language gates are
# invoked from the --full branch of tools/gate.sh; opt back out per-repo with
# GOH_SWIFT_COLD=0 in .gatesrc if a run genuinely cannot afford it. SPM mode
# wipes .build; xcode mode wipes the pinned derived-data tree (.build/xcode-dd).
#
#   swift_gate.sh [repo]
#
# Config, from the target repo's .gatesrc:
#   GOH_SWIFT_MODE=spm|xcode   default: spm if Package.swift exists
#   GOH_SWIFT_SCHEME=Foo       required for xcode mode
#   GOH_SWIFT_PROJECT=Foo.xcodeproj
#   GOH_SWIFT_COV_MIN=95
#   GOH_SWIFT_COLD=1     # default: 1 — set to 0 to allow incremental builds
#   GOH_SWIFT_LINT_BASELINE=path/to/.swiftlint-baseline.json
#       when set, the lint stage runs against this SwiftLint baseline as a
#       SHRINK-ONLY ratchet: listed violations are tolerated, NEW ones fail
#       naming them, and entries whose violations vanished are a printed
#       nudge to re-record — never a failure. Unset keeps bare strict
#       linting. Semantics match swiftlint's own --baseline (probed 0.65.1):
#       a violation is "the same one" by file + rule + reason, NOT line or
#       severity.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

repo="${1:-$PWD}"
cd "$repo"
[ -f .gatesrc ] && . ./.gatesrc

MODE="${GOH_SWIFT_MODE:-}"
if [ -z "$MODE" ]; then
    if [ -f Package.swift ]; then MODE="spm"; else MODE="xcode"; fi
fi

goh_init "swift"

command -v swift >/dev/null 2>&1 || die "swift not on PATH"

# swift_lint_with_baseline — the GOH_SWIFT_LINT_BASELINE branch. swiftlint's
# own exit status is deliberately NOT trusted here: in --reporter json mode it
# reports violations FOUND, which is the ratchet's input, not its verdict —
# the reconciler decides. A crashed swiftlint still bites: unparseable output
# fails the gate naming swiftlint's exit code.
swift_lint_with_baseline() {
    [ -f "$GOH_SWIFT_LINT_BASELINE" ] \
        || die "GOH_SWIFT_LINT_BASELINE points at nothing: $GOH_SWIFT_LINT_BASELINE"
    local report rc=0
    report="$(mktemp -t goh-swiftlint-report)"
    step "swiftlint --strict (baseline: $(basename "$GOH_SWIFT_LINT_BASELINE"))"
    swiftlint lint --strict --quiet --reporter json >"$report" || rc=$?
    if ! python3 "$HERE/swift_lint_baseline.py" \
            --baseline "$GOH_SWIFT_LINT_BASELINE" \
            --report "$report" --root "$PWD" \
            --swiftlint-rc "$rc"; then
        rm -f "$report"
        die "swift gate: lint baseline ratchet failed — fix the new violations or re-record (shrink-only)"
    fi
    rm -f "$report"
}

if command -v swiftlint >/dev/null 2>&1; then
    if [ -n "${GOH_SWIFT_LINT_BASELINE:-}" ]; then
        swift_lint_with_baseline
    else
        goh_step "swiftlint --strict" swiftlint --strict
    fi
else
    warn "swiftlint not installed — lint gate skipped (brew install swiftlint)"
fi

if [ "${GOH_SWIFT_COLD:-1}" = "1" ]; then
    step "cold build requested — clearing derived products"
    rm -rf .build .build/xcode-dd
fi

case "$MODE" in
  spm)
    goh_step "build (warnings as errors)" \
        swift build -Xswiftc -warnings-as-errors
    if [ -n "${GOH_SWIFT_COV_MIN:-}" ]; then
        goh_step "test + coverage" swift test --enable-code-coverage
        goh_step "coverage >= ${GOH_SWIFT_COV_MIN}%" \
            python3 "$HERE/../checks/check_swift_coverage.py" --min "$GOH_SWIFT_COV_MIN"
    else
        goh_step "test" swift test
        warn "no coverage floor — set GOH_SWIFT_COV_MIN in .gatesrc"
    fi
    ;;
  xcode)
    [ -n "${GOH_SWIFT_SCHEME:-}" ] || die "xcode mode needs GOH_SWIFT_SCHEME in .gatesrc"
    proj="${GOH_SWIFT_PROJECT:-$(ls -d ./*.xcodeproj 2>/dev/null | head -1)}"
    [ -n "$proj" ] || die "no .xcodeproj found and GOH_SWIFT_PROJECT is unset"
    # Pinned derived data: cold builds know what to wipe, and
    # check_swift_coverage.py knows where the xcresult lands.
    dd=".build/xcode-dd"
    goh_step "xcodebuild test (-scheme $GOH_SWIFT_SCHEME)" \
        xcodebuild test -project "$proj" -scheme "$GOH_SWIFT_SCHEME" \
        -derivedDataPath "$dd" -enableCodeCoverage YES
    if [ -n "${GOH_SWIFT_COV_MIN:-}" ]; then
        goh_step "coverage >= ${GOH_SWIFT_COV_MIN}%" \
            python3 "$HERE/../checks/check_swift_coverage.py" --min "$GOH_SWIFT_COV_MIN" --xcode --dd "$dd"
    fi
    ;;
  *) die "unknown GOH_SWIFT_MODE: $MODE (want spm or xcode)" ;;
esac

goh_done
