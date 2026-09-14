#!/usr/bin/env bash
# Rust gate for goh. fmt --check, clippy pedantic -D warnings,
# machete (no unused deps), audit that bites, cargo tests.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "→ cargo fmt --check"
cargo fmt --all -- --check
echo "→ cargo clippy -D warnings"
cargo clippy --workspace --all-targets -- -D warnings
echo "→ cargo machete"
cargo machete
echo "→ cargo audit (biting)"
cargo audit --deny warnings --deny unmaintained --deny unsound --deny yanked
echo "→ cargo test"
cargo test --workspace --quiet
echo "✓ rust gate passed"
