#!/usr/bin/env bash
# rust_gate.sh — the Rust gates every house Rust project must pass.
#
#   1. cargo fmt --all -- --check
#   2. cargo clippy --workspace --all-targets --all-features -- -D warnings
#   3. tools/check_no_allow.py — no #[allow]; fix findings, never silence.
#      (Repo-local tool; skipped with a warning when not installed.)
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

goh_optional_step "no #[allow]" tools/check_no_allow.py \
    python3 tools/check_no_allow.py

goh_done
