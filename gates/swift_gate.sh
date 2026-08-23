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
# targets were never re-examined. GOH_SWIFT_COLD=1 (the default in --full)
# wipes .build first. This is slow ON PURPOSE.
#
#   swift_gate.sh [repo]
#
# Config, from the target repo's .gatesrc:
#   GOH_SWIFT_MODE=spm|xcode   default: spm if Package.swift exists
#   GOH_SWIFT_SCHEME=Foo       required for xcode mode
#   GOH_SWIFT_PROJECT=Foo.xcodeproj
#   GOH_SWIFT_COV_MIN=95
#   GOH_SWIFT_COLD=1
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

if command -v swiftlint >/dev/null 2>&1; then
    goh_step "swiftlint --strict" swiftlint --strict
else
    warn "swiftlint not installed — lint gate skipped (brew install swiftlint)"
fi

if [ "${GOH_SWIFT_COLD:-0}" = "1" ]; then
    step "cold build requested — clearing derived products"
    rm -rf .build
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
    goh_step "xcodebuild test (-scheme $GOH_SWIFT_SCHEME)" \
        xcodebuild test -project "$proj" -scheme "$GOH_SWIFT_SCHEME" -enableCodeCoverage YES
    if [ -n "${GOH_SWIFT_COV_MIN:-}" ]; then
        goh_step "coverage >= ${GOH_SWIFT_COV_MIN}%" \
            python3 "$HERE/../checks/check_swift_coverage.py" --min "$GOH_SWIFT_COV_MIN" --xcode
    fi
    ;;
  *) die "unknown GOH_SWIFT_MODE: $MODE (want spm or xcode)" ;;
esac

goh_done
