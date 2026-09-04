#!/usr/bin/env bash
# rust_manifest_gate.sh — fail when CARGO'S OWN lints fire.
#
# WHY THIS IS A SEPARATE STEP FROM CLIPPY
#
# `cargo clippy -- -D warnings` denies RUSTC lints. Everything after `--` is a
# rustc lint level, and cargo has a second, independent lint namespace of its
# own (`cargo::unused_dependencies` and friends) whose levels that flag never
# touches. Cargo emits those as warnings and still exits 0.
#
# Measured on routines, 2026-09-04: three dead dependencies printed a warning
# on every single build, rode through fourteen green gate steps, and shipped in
# a tagged release. `cargo clippy --workspace --all-targets --all-features --
# -D warnings` printed two of them and exited 0. A gate that reads only the
# exit code of a command that will not fail is not a gate.
#
# So this step runs cargo and reads its OUTPUT, because the exit code is not
# where the answer is.
#
#   rust_manifest_gate.sh              # run from the cargo dir (uses $PWD)
#   rust_manifest_gate.sh <cargo_dir>  # cargo lives elsewhere
#
# WHY NOT --all-targets
#
# Deliberate, and it is the stricter choice here. With `--all-targets`, cargo
# builds the test targets too, so a crate listed under `[dependencies]` that is
# only ever used by tests counts as USED and is never flagged — even though
# belonging in `[dev-dependencies]` is exactly what it means. Routines had that
# case: the plain build reported three unused deps, the `--all-targets` clippy
# run reported two, and the third was a `tempfile` declared in both tables.
#
# STATED GAP: the mirror image is not covered. An unused entry under
# `[dev-dependencies]` needs the test targets built to be seen, so this
# invocation cannot report one. That is a smaller defect (dev-deps do not ship)
# and catching it would mean a second full check; if that trade ever changes,
# add a second pass rather than swapping this one's flags and silently losing
# the case above.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/Users/ztomer/Projects/gates_of_heck/gates/_common.sh
. "$HERE/_common.sh"

cd "${1:-$PWD}"

log="$(mktemp -t goh-cargo-manifest)"
trap 'rm -f "$log"' EXIT

# A compile failure is a real failure and must not be swallowed while looking
# for warnings. Reported as itself, not as a manifest finding.
if ! cargo check --workspace --all-features >"$log" 2>&1; then
    tail -n "${GOH_TAIL:-60}" "$log" >&2
    die "[cargo_manifest] cargo check failed"
fi

# Cargo prints one summary line per crate whose manifest produced findings.
# Matching the SUMMARY rather than a specific lint name is what makes this
# blind to nothing: a cargo lint added in a future toolchain is caught the day
# it starts firing, with no edit here and no per-repo opt-in.
if grep -qE '^warning: .*\(manifest\) generated' "$log"; then
    printf '\n'
    # The whole warning BLOCK, not a hand-picked set of line shapes. The first
    # draft of this printed only `unused dependency` lines plus any `-->`
    # pointing at a Cargo.toml -- so the day it caught a different cargo lint
    # (`non_kebab_case_bins`, immediately) it failed while showing bare `-->`
    # arrows and no message at all. A gate that cannot say what it found sends
    # you to run the command by hand, which is most of the way back to not
    # having the gate. Match the summary to DECIDE (blind to nothing), print
    # the context to EXPLAIN.
    grep -E -A7 '^warning:' "$log" >&2 || true
    printf '\n'
    err "[cargo_manifest] cargo's own lints fired — see the findings above."
    err "  These never fail clippy: \`-D warnings\` sets RUSTC lint levels and"
    err "  cargo's lint namespace is separate, so cargo warns and exits 0."
    err "  Fix the manifest. If a dependency is a false positive (used only"
    err "  behind a cfg or a macro), state that in the crate's Cargo.toml:"
    err "      [lints.cargo]"
    err "      unused_dependencies = \"allow\"   # why, in a comment"
    exit 1
fi

ok "[cargo_manifest] no cargo-lint findings"
