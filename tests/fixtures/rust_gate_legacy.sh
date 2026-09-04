#!/usr/bin/env bash
# rust_gate.sh — shared Rust quality gate for all house Rust projects.
#
# Runs the three structural gates every Rust repo must pass:
#   1. cargo fmt --all -- --check
#   2. cargo clippy --workspace --all-targets --all-features -- -D warnings
#   3. tools/check_no_allow.py (no #[allow] — fix findings, never silence)
#
# Pre-commit hooks and CI in each repo call this one script so the gate is
# written ONCE and enforced everywhere (house rule: structural, not
# disciplinary). Exit non-zero on the first failing gate.
#
#   rust_gate.sh                       # run from repo root (uses $PWD)
#   rust_gate.sh <repo>                # run from anywhere
#   rust_gate.sh <repo> <cargo_dir>    # cargo lives in a subdir (e.g. divoomd/)
#
# When <cargo_dir> is given, fmt/clippy run there while the no-#[allow] checker
# still scans the whole repo from its root.
#
# NOTE: sccache is expected via RUSTC_WRAPPER (see ~/.zshenv). cargo check is
# non-cacheable; the real cache win is on the build/test steps.
set -euo pipefail

repo="${1:-$PWD}"
cargo_dir="${2:-$repo}"
cd "$repo"
cargo_dir="$(cd "$cargo_dir" && pwd)"

command -v cargo >/dev/null 2>&1 || { echo "✗ cargo not on PATH" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "✗ python3 not on PATH" >&2; exit 1; }

# House TUI lib when present (repo-local or shared), else plain output.
GOH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f tui/lib.sh ]; then . tui/lib.sh            # vendored into the target repo
elif [ -f "$GOH_ROOT/tui/lib.sh" ]; then . "$GOH_ROOT/tui/lib.sh"   # alongside this script
fi
info() { echo "→ $*"; }
ok()   { echo "✓ $*"; }
err()  { echo "✗ $*" >&2; }
warn() { echo "⚠ $*" >&2; }
die()  { err "$*"; exit 1; }
# Overwrite the fallbacks with the real lib if it provided them.
if command -v _lib_info >/dev/null 2>&1; then
    info(){ _lib_info "$*"; }; ok(){ _lib_ok "$*"; }
    err(){ _lib_err "$*"; };  warn(){ _lib_warn "$*"; }
fi

log="$(mktemp -t rustgate)"
trap 'rm -f "$log"' EXIT

section() { printf '\n== %s ==\n' "$1"; }

section "fmt"
if ! (cd "$cargo_dir" && cargo fmt --all -- --check) >"$log" 2>&1; then
    tail -40 "$log" >&2
    die "formatting is dirty — run: cargo fmt --all"
fi
ok "formatting clean"

section "clippy (-D warnings, all targets, all features)"
if ! (cd "$cargo_dir" && cargo clippy --workspace --all-targets --all-features -- -D warnings) >"$log" 2>&1; then
    tail -60 "$log" >&2
    die "clippy is not clean — fix the warnings, do not add #[allow]"
fi
ok "clippy clean"

section "no #[allow]"
if [ -f tools/check_no_allow.py ]; then
    if ! python3 tools/check_no_allow.py >"$log" 2>&1; then
        cat "$log" >&2
        die "an #[allow] is present — fix the finding properly"
    fi
    ok "no allow attributes"
else
    warn "tools/check_no_allow.py missing — install from scripts/templates"
fi

printf '\nAll Rust gates passed.\n'
