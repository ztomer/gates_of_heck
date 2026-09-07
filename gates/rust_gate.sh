#!/usr/bin/env bash
# rust_gate.sh — the Rust gates every house Rust project must pass.
#
#   1. cargo fmt --all -- --check
#   2. cargo clippy --workspace --all-targets --all-features -- -D warnings
#   2b. gates/rust_manifest_gate.sh — CARGO's own lints, which step 2 cannot
#      see: `-D warnings` sets rustc lint levels, cargo's namespace is
#      separate, so cargo warns and exits 0. Reads output, not exit code.
#   2c. EVERY SHIPPED CONFIGURATION, from GOH_RUST_LINT_CONFIGS. Step 2 lints
#      one cfg: this machine's target, with all features on. A crate that is
#      part `cfg(target_os = ...)` or part `cfg(feature = ...)` has halves that
#      command never compiles, so it cannot report on them. Unset keeps step 2
#      alone, with a printed nudge.
#   2d. checks/check_lints_optin.py — a workspace's [workspace.lints] is a
#      DECLARATION; a member applies it with `[lints] workspace = true`. A crate
#      that never says so inherits nothing and looks clean.
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

# EVERY SHIPPED CONFIGURATION, NOT JUST THIS ONE.
#
# `cargo clippy` reports on the cfg it compiled for and is silent about every
# other. A crate that is half `cfg(target_os = "macos")` is checked by a Mac and
# by an ubuntu runner, each seeing its own half, and NEITHER failing on the
# other's code -- so between them the halves look covered while nothing compares
# them. Measured on `monitor`, 2026-09-07: turning the workspace lints on for
# one crate found 26 findings on the host and 48 for linux-musl, ten of which
# were in src the host cannot see, plus two `#[expect]`s that were UNFULFILLED
# there -- and an unfulfilled expectation under `-D warnings` is an error, so
# the build was broken for the platform that ships while every gate was green.
#
# Each entry is extra argv for clippy, ':'-separated, e.g.
#   GOH_RUST_LINT_CONFIGS='--target x86_64-unknown-linux-musl -p agent:-p d --no-default-features'
if [ -n "${GOH_RUST_LINT_CONFIGS:-}" ]; then
    installed="$(rustup target list --installed 2>/dev/null || true)"
    saved_ifs="$IFS"; IFS=':'
    for cfg in $GOH_RUST_LINT_CONFIGS; do
        IFS="$saved_ifs"
        [ -n "$cfg" ] || continue
        # A missing target std would make clippy inspect NOTHING while exiting
        # non-zero for an unrelated reason. Name the fix instead.
        case "$cfg" in
            *--target*)
                tgt="$(printf '%s\n' "$cfg" | sed -n 's/.*--target[= ]\([^ ]*\).*/\1/p')"
                if [ -n "$tgt" ] && ! printf '%s\n' "$installed" | grep -qx "$tgt"; then
                    err "[rust] target $tgt is not installed; this step would inspect nothing."
                    err "  rustup target add $tgt"
                    exit 1
                fi
                ;;
        esac
        # shellcheck disable=SC2086
        goh_step_in "$cargo_dir" "clippy ($cfg)" \
            cargo clippy --all-targets $cfg -- -D warnings
        IFS=':'
    done
    IFS="$saved_ifs"
else
    warn "only this machine's cfg was linted — set GOH_RUST_LINT_CONFIGS in .gatesrc"
    warn "  for every target and feature set the crate actually ships to"
fi

goh_step "lint policy is inherited" \
    python3 "$HERE/../checks/check_lints_optin.py"

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
