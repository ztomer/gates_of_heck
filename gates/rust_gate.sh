#!/usr/bin/env bash
# rust_gate.sh — the Rust gates every house Rust project must pass.
#
#   1. cargo fmt --all -- --check
#   2. cargo clippy --workspace --all-targets --all-features -- -D warnings
#   2b. gates/rust_manifest_gate.sh — CARGO's own lints, which step 2 cannot
#      see: `-D warnings` sets rustc lint levels, cargo's namespace is
#      separate, so cargo warns and exits 0. Reads output, not exit code.
#   3. tools/check_no_allow.py — no #[allow]; fix findings, never silence.
#      (Repo-local tool; skipped with a warning when not installed.)
#   4. coverage floor, when stated (GOH_COV_FLOOR_RUST or GOH_COV_FLOORS_JSON
#      in .gatesrc) — via gates/coverage_gate.sh, with the floor passed as
#      explicit argv (.gatesrc values are shell variables, NOT exported, so
#      the callee cannot see them through the environment). Unset keeps
#      fmt+clippy only, with a printed nudge (the py/swift-gate precedent).
#
#   rust_gate.sh                       # run from repo root (uses $PWD)
#   rust_gate.sh <repo>                # run from anywhere
#   rust_gate.sh <repo> <cargo_dir>    # cargo lives in a subdir (e.g. divoomd/)
#
# When <cargo_dir> is given, fmt/clippy run there while the no-#[allow]
# checker still scans from the repo root.
#
# NOTE: sccache is expected via RUSTC_WRAPPER (see ~/.zshenv). fmt and clippy
# are largely cache-hostile; the real cache win is on build/test steps.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

repo="${1:-$PWD}"
cargo_dir="${2:-$repo}"
cd "$repo"
cargo_dir="$(cd "$cargo_dir" && pwd)"

[ -f .gatesrc ] && . ./.gatesrc

goh_init "rust"

command -v cargo >/dev/null 2>&1 || die "cargo not on PATH"

goh_step_in "$cargo_dir" "fmt" cargo fmt --all -- --check

goh_step_in "$cargo_dir" "clippy (-D warnings, all targets, all features)" \
    cargo clippy --workspace --all-targets --all-features -- -D warnings

# Cargo's own lint namespace. Separate from clippy because clippy CANNOT fail
# on these -- see the header of the script for the measurement that proved it.
goh_step_in "$cargo_dir" "cargo lints (manifest)" \
    bash "$HERE/rust_manifest_gate.sh" "$cargo_dir"

goh_optional_step "no #[allow]" tools/check_no_allow.py \
    python3 tools/check_no_allow.py

if [ -n "${GOH_COV_FLOOR_RUST:-}" ]; then
    goh_step "coverage (floor ${GOH_COV_FLOOR_RUST}%)" \
        bash "$HERE/coverage_gate.sh" --lang rust \
        --floor "$GOH_COV_FLOOR_RUST" "$cargo_dir"
elif [ -n "${GOH_COV_FLOORS_JSON:-}" ]; then
    goh_step "coverage (per-target floors)" \
        bash "$HERE/coverage_gate.sh" --lang rust \
        --floors-json "$GOH_COV_FLOORS_JSON" "$cargo_dir"
else
    warn "no coverage floor — set GOH_COV_FLOOR_RUST in .gatesrc"
fi

goh_done
